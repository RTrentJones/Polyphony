"""Book, chapter, and draft endpoints: the book-writing workflow."""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import Select, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.jobs import latest_jobs
from app.characters.cast import resolve_cast
from app.core import estimates
from app.core.budget import check_user_budget, remaining_budget_24h
from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    Chapter as ChapterORM,
    Character as CharacterORM,
    Scene as SceneORM,
    SceneRevision as SceneRevisionORM,
    Source as SourceORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.exports.builder import (
    CONTENT_TYPES,
    BookExport,
    ChapterExport,
    SceneExport,
    scene_text,
    select_chapters,
    to_docx,
    to_epub,
    to_markdown,
)
from app.jobs import repository as jobs_repo
from app.jobs import scene_batch
from app.llm.tier import get_tier
from app.planning.canon import build_canon, render_character
from app.planning.outline import CastIndex, classify_people
from app.planning.planned_beat import (
    MAX_PLANNED_BEATS,
    PlannedBeat,
    beat_scene_description,
    normalize_planned_beats,
    unwritten_beats,
)
from app.versioning import entities as entity_versions
from app.versioning import trash
from app.versioning.synopsis import record_synopsis
from app.versioning.trash import live, renumber_two_phase as _renumber_two_phase

router = APIRouter()


# --- Payloads -----------------------------------------------------------------


class BookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    author: Optional[str] = None
    synopsis: Optional[str] = None
    genre: Optional[str] = None


class BookUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    author: Optional[str] = None
    synopsis: Optional[str] = None
    genre: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(drafting|revising|complete)$")


class ChapterCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: Optional[str] = None
    # Append when omitted. Non-negative: negative positions are reserved as
    # temp parking slots by the two-phase renumber (see _renumber_two_phase).
    position: Optional[int] = Field(None, ge=0)


class ChapterUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    summary: Optional[str] = None
    status: Optional[str] = None
    # The chapter's planned scenes (SPEC-23), replacing the stored list. Strict:
    # a character id that is not live in this book is a 422.
    planned_beats: Optional[list[PlannedBeat]] = Field(
        None, max_length=MAX_PLANNED_BEATS
    )


class PositionUpdate(BaseModel):
    position: int = Field(..., ge=0)


class ChapterSceneRequest(BaseModel):
    source_id: Optional[UUID] = None  # provenance only
    # The cast, as character IDs of this book (SPEC-01). The deprecated
    # `characters` (names) input was removed after its grace release (SPEC-15c);
    # an unknown ID is rejected rather than silently generated voice-blind.
    character_ids: list[UUID] = Field(default_factory=list)
    scene_description: str = Field(..., min_length=10)
    setting: str
    emotional_tone: str
    pov_character: Optional[str] = None
    target_word_count: int = Field(default=800, ge=100, le=3000)
    style_notes: Optional[str] = None
    # Generation mode (docs/BRD.md §8). prose = one call per beat (default);
    # ensemble = narrator/character/editor loop, opt-in and tier-gated.
    mode: str = Field(default="prose", pattern="^(prose|ensemble)$")
    # The planned beat this scene is written from (SPEC-23): links the scene to
    # the beat, so "Generate all planned scenes" counts that beat as written.
    planned_beat_id: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_names_input(cls, data):
        # A stale client still sending the removed by-name cast gets a clear 422
        # instead of having its cast silently dropped.
        if isinstance(data, dict) and "characters" in data:
            raise ValueError(
                "`characters` (names) was removed; send the cast as `character_ids`"
            )
        return data


class PlannedScenesRequest(BaseModel):
    """Generate every planned scene of a chapter (SPEC-10 follow-up, SPEC-23).
    Each scene is written from its planned beat: its description, cast,
    setting and tone. A field set here overrides every beat's."""

    # Non-empty = the cast of EVERY scene. Empty = each beat's own cast, else
    # the chapter's promoted cast.
    character_ids: list[UUID] = Field(default_factory=list)
    # "" = each beat's setting / tone ("" allowed: the style guide governs).
    setting: str = ""
    emotional_tone: str = ""
    target_word_count: int = Field(default=800, ge=100, le=3000)
    style_notes: Optional[str] = None
    mode: str = Field(default="prose", pattern="^(prose|ensemble)$")


class SceneContentUpdate(BaseModel):
    content: str = Field(..., min_length=1)


class SceneUpdate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]


class ManualSceneCreate(BaseModel):
    """Create a scene WITHOUT LLM generation — a blank/manual draft to write in."""

    title: Optional[str] = None
    content: str = ""
    characters: list[str] = Field(default_factory=list)
    setting: str = ""
    emotional_tone: str = ""


# --- Helpers -------------------------------------------------------------------


async def _owned_book(book_id: UUID, user: UserORM, db: AsyncSession) -> BookORM:
    book = (
        await db.execute(
            select(BookORM).where(BookORM.id == book_id, BookORM.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


async def _owned_chapter(
    chapter_id: UUID, user: UserORM, db: AsyncSession
) -> ChapterORM:
    chapter = (
        await db.execute(
            live(select(ChapterORM), ChapterORM)
            .join(BookORM, ChapterORM.book_id == BookORM.id)
            .where(ChapterORM.id == chapter_id, BookORM.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not chapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found"
        )
    return chapter


async def owned_scene(scene_id: UUID, user: UserORM, db: AsyncSession, *options):
    """A scene, authorised through its book (SPEC-04): the book is the root, and
    `Scene.user_id` stays as the tenant guard. 404 for anyone else's scene."""
    scene = (
        await db.execute(
            live(select(SceneORM), SceneORM)
            .options(*options)
            .join(BookORM, SceneORM.book_id == BookORM.id)
            .where(
                SceneORM.id == scene_id,
                BookORM.user_id == user.id,
                SceneORM.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )
    return scene


def book_order(query: Select) -> Select:
    """The ONE book order (SPEC-37): live chapters by position, then scene
    position; unfiled scenes last, newest first."""
    return query.outerjoin(
        ChapterORM,
        and_(SceneORM.chapter_id == ChapterORM.id, ChapterORM.deleted_at.is_(None)),
    ).order_by(
        ChapterORM.position.nulls_last(),
        SceneORM.position,
        SceneORM.created_at.desc(),
    )


async def lock_scene(db: AsyncSession, scene: SceneORM) -> SceneORM:
    """Row-lock an owned scene and re-read it, so a write that checks the draft
    (accepting a revision) and one that snapshots it (a manual save) cannot
    both read the same draft and overwrite each other."""
    return (
        await db.execute(
            select(SceneORM)
            .where(SceneORM.id == scene.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def snapshot_draft(db: AsyncSession, scene: SceneORM) -> None:
    """Keep the current draft as restorable history before it is replaced (a
    manual save, an accepted revision): raw, so history restores it byte for
    byte, and "edited" once the author has saved over the generation."""
    previous = scene.draft
    if previous:
        db.add(
            SceneRevisionORM(
                scene_id=scene.id,
                content=previous,
                word_count=len(previous.split()),
                source="edited" if scene.content else "generated",
            )
        )


def _chapter_dict(c: ChapterORM, index: CastIndex) -> dict:
    people = chapter_people(c, index)
    return {
        "id": str(c.id),
        "book_id": str(c.book_id),
        "position": c.position,
        "title": c.title,
        "summary": c.summary,
        "status": c.status,
        "number": c.number,  # the one "chapter N" (S9)
        # From the outline node it was promoted from (SPEC-10): scene defaults.
        "plan_node_id": str(c.plan_node_id) if c.plan_node_id else None,
        "pov_character_id": str(c.pov_character_id) if c.pov_character_id else None,
        "character_ids": c.character_ids or [],
        # The planned scenes as data (SPEC-23), each with the cast it is
        # written with against the live cast (SPEC-32; derived, never stored).
        "planned_beats": [
            {**b, "cast_ids": beat_people(b, index, *people)[0]}
            for b in normalize_planned_beats(c.planned_beats)
        ],
    }


# --- Books ----------------------------------------------------------------------


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_book(
    payload: BookCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = BookORM(
        user_id=current_user.id,
        title=payload.title,
        author=payload.author,
        genre=payload.genre,
    )
    db.add(book)
    await db.flush()
    if payload.synopsis:
        # Synopsis is Canon: create -> v1 (PR review #2).
        await record_synopsis(
            db, book, payload.synopsis, reason="created", created_by=current_user.id
        )
    await db.commit()
    await db.refresh(book)
    return {"id": str(book.id), "title": book.title, "status": book.status}


@router.get("/", response_model=dict)
async def list_books(
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The author's books, each with its progress (SPEC-27, B12): words drafted,
    chapters and scenes drafted out of planned, and the last-edited scene (the
    dashboard's "Resume" link). Four queries for any number of books."""
    books = (
        (
            await db.execute(
                select(BookORM)
                .where(BookORM.user_id == current_user.id)
                .order_by(BookORM.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    book_ids = [b.id for b in books]
    progress = {
        b.id: {
            "word_count": 0,
            "chapter_count": 0,
            "chapters_drafted": 0,
            "scenes_drafted": 0,
            "scenes_planned": 0,
            "last_edited_scene": None,
        }
        for b in books
    }
    if book_ids:
        chapters = (
            (
                await db.execute(
                    live(select(ChapterORM), ChapterORM).where(
                        ChapterORM.book_id.in_(book_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        chapter_titles = {c.id: c.title for c in chapters}
        for c in chapters:
            progress[c.book_id]["chapter_count"] += 1
            progress[c.book_id]["scenes_planned"] += len(
                normalize_planned_beats(c.planned_beats)
            )

        # Live scenes in a live chapter, or standalone (no chapter).
        in_book = (
            SceneORM.book_id.in_(book_ids),
            SceneORM.deleted_at.is_(None),
            (SceneORM.chapter_id.is_(None)) | (ChapterORM.deleted_at.is_(None)),
        )
        scenes_from = (
            select()
            .select_from(SceneORM)
            .outerjoin(ChapterORM, ChapterORM.id == SceneORM.chapter_id)
        )
        drafted = SceneORM.status == "completed"
        for book_id, words, n_drafted in await db.execute(
            scenes_from.add_columns(
                SceneORM.book_id,
                func.coalesce(func.sum(SceneORM.word_count), 0),
                func.count(SceneORM.id).filter(drafted),
            )
            .where(*in_book)
            .group_by(SceneORM.book_id)
        ):
            progress[book_id]["word_count"] = int(words or 0)
            progress[book_id]["scenes_drafted"] = int(n_drafted or 0)

        for book_id, n_chapters in await db.execute(
            scenes_from.add_columns(
                SceneORM.book_id, func.count(func.distinct(SceneORM.chapter_id))
            )
            .where(*in_book, drafted, SceneORM.chapter_id.is_not(None))
            .group_by(SceneORM.book_id)
        ):
            progress[book_id]["chapters_drafted"] = int(n_chapters or 0)

        # The most recently edited live scene per book (ties: newest id wins).
        rank = (
            func.row_number()
            .over(
                partition_by=SceneORM.book_id,
                order_by=(SceneORM.updated_at.desc(), SceneORM.id.desc()),
            )
            .label("rank")
        )
        latest = (
            scenes_from.add_columns(
                SceneORM.book_id,
                SceneORM.id,
                SceneORM.title,
                SceneORM.chapter_id,
                SceneORM.updated_at,
                rank,
            )
            .where(*in_book)
            .subquery()
        )
        for book_id, scene_id, title, chapter_id, updated_at, _ in await db.execute(
            select(latest).where(latest.c.rank == 1)
        ):
            progress[book_id]["last_edited_scene"] = {
                "id": str(scene_id),
                "title": title,
                "chapter_id": str(chapter_id) if chapter_id else None,
                "chapter_title": chapter_titles.get(chapter_id),
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
    return {
        "books": [
            {
                "id": str(b.id),
                "title": b.title,
                "author": b.author,
                "genre": b.genre,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                **progress[b.id],
            }
            for b in books
        ]
    }


@router.get("/{book_id}", response_model=dict)
async def get_book(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    chapters = (
        (
            await db.execute(
                live(select(ChapterORM), ChapterORM)
                .where(ChapterORM.book_id == book.id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )
    index = await book_cast_index(db, book.id)
    return {
        "id": str(book.id),
        "title": book.title,
        "author": book.author,
        "synopsis": book.synopsis,
        "genre": book.genre,
        "status": book.status,
        "chapters": [_chapter_dict(c, index) for c in chapters],
        "character_count": len(index.names),  # SPEC-32
    }


@router.patch("/{book_id}", response_model=dict)
async def update_book(
    book_id: UUID,
    payload: BookUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    # The synopsis is Canon (docs/BRD.md §3, R4) — route every change through the
    # centralized versioner so the prior value is preserved and restorable and the
    # invariant create->v1 / edit->v2 holds (PR review #2).
    synopsis_changed = payload.synopsis is not None and payload.synopsis != (
        book.synopsis or ""
    )
    for field_name in ("title", "author", "genre", "status"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(book, field_name, value)
    if synopsis_changed:
        await record_synopsis(
            db, book, payload.synopsis, reason="edited", created_by=current_user.id
        )
    await db.commit()
    return {"id": str(book.id), "title": book.title, "status": book.status}


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    await db.delete(book)
    await db.commit()
    return None


# --- Chapters --------------------------------------------------------------------


@router.post(
    "/{book_id}/chapters", response_model=dict, status_code=status.HTTP_201_CREATED
)
async def create_chapter(
    book_id: UUID,
    payload: ChapterCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_book(book_id, current_user, db)
    user_id = current_user.id  # a plain value survives the rollback below
    if payload.position is None:
        # Append: concurrent appends can both compute max+1; the UNIQUE
        # (book_id, position) constraint turns that into an IntegrityError —
        # recompute and retry once instead of silently duplicating.
        for attempt in (1, 2):
            max_pos = (
                await db.execute(
                    live(
                        select(func.coalesce(func.max(ChapterORM.position), -1)),
                        ChapterORM,
                    ).where(ChapterORM.book_id == book_id)
                )
            ).scalar_one()
            chapter = ChapterORM(
                book_id=book_id,
                title=payload.title,
                summary=payload.summary,
                position=max_pos + 1,
            )
            db.add(chapter)
            try:
                await entity_versions.record(
                    db, "chapter", chapter, reason="created", created_by=user_id
                )
                await db.commit()
                break
            except IntegrityError:
                await db.rollback()
                if attempt == 2:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Chapter position conflict; please retry",
                    )
    else:
        # Insert-at: shift siblings at >= the requested position up by one
        # (two-phase: park on negatives, flush, land shifted positions).
        to_shift = (
            (
                await db.execute(
                    live(select(ChapterORM), ChapterORM)
                    .where(
                        ChapterORM.book_id == book_id,
                        ChapterORM.position >= payload.position,
                    )
                    .order_by(ChapterORM.position)
                )
            )
            .scalars()
            .all()
        )
        original_positions = [c.position for c in to_shift]
        for idx, c in enumerate(to_shift):
            c.position = -(idx + 1)
        await db.flush()
        for c, pos in zip(to_shift, original_positions):
            c.position = pos + 1
        chapter = ChapterORM(
            book_id=book_id,
            title=payload.title,
            summary=payload.summary,
            position=payload.position,
        )
        db.add(chapter)
        await entity_versions.record(
            db, "chapter", chapter, reason="created", created_by=current_user.id
        )
        await db.commit()
    await db.refresh(chapter)
    return _chapter_dict(chapter, await book_cast_index(db, book_id))


@router.get("/chapters/{chapter_id}", response_model=dict)
async def get_chapter(
    chapter_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    scenes = (
        (
            await db.execute(
                live(select(SceneORM), SceneORM)
                .where(SceneORM.chapter_id == chapter.id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )
    jobs = await latest_jobs(db, "scene", [s.id for s in scenes])
    return {
        **_chapter_dict(chapter, await book_cast_index(db, chapter.book_id)),
        "scenes": [
            {
                "id": str(s.id),
                "job": jobs.get(str(s.id)),
                "position": s.position,
                "status": s.status,
                "word_count": s.word_count,
                "preview": (scene_text(s)[:200] or None),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenes
        ],
    }


async def _strict_planned_beats(
    db: AsyncSession, book_id: UUID, beats: list[PlannedBeat]
) -> list[dict]:
    """The author's planned beats, normalized; 422 naming every character id
    that is not a live character of this book (the author's own edit is
    strict, like an outline PUT)."""
    live_ids = {
        str(cid)
        for cid in (
            await db.execute(
                live(select(CharacterORM.id), CharacterORM).where(
                    CharacterORM.book_id == book_id
                )
            )
        ).scalars()
    }
    unknown: list[str] = []
    for beat in beats:
        for cid in beat.character_ids:
            try:
                key = str(UUID(str(cid)))
            except ValueError:
                key = None
            if key not in live_ids and cid not in unknown:
                unknown.append(cid)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "These characters are not in this book's cast. Add "
                "them in the Canon tab first, or pick from the cast.",
                "unknown": unknown,
            },
        )
    return normalize_planned_beats([b.model_dump() for b in beats])


@router.patch("/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    chapter_id: UUID,
    payload: ChapterUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    changes = {
        f: getattr(payload, f)
        for f in ("title", "summary", "status")
        if getattr(payload, f) is not None
        and getattr(payload, f) != getattr(chapter, f)
    }
    if payload.planned_beats is not None:
        beats = await _strict_planned_beats(db, chapter.book_id, payload.planned_beats)
        if beats != normalize_planned_beats(chapter.planned_beats):
            changes["planned_beats"] = beats
    if changes:
        # Chapter titles, summaries and planned beats are versioned (SPEC-13,
        # F16, SPEC-23); a chapter from before then keeps its state as v1.
        await entity_versions.ensure_baseline(
            db, "chapter", chapter, created_by=current_user.id
        )
        for field_name, value in changes.items():
            setattr(chapter, field_name, value)
        await entity_versions.record(
            db, "chapter", chapter, reason="edited", created_by=current_user.id
        )
    await db.commit()
    return _chapter_dict(chapter, await book_cast_index(db, chapter.book_id))


@router.patch("/chapters/{chapter_id}/position", response_model=dict)
async def move_chapter(
    chapter_id: UUID,
    payload: PositionUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder: shift siblings between old and new positions."""
    chapter = await _owned_chapter(chapter_id, current_user, db)
    siblings = (
        (
            await db.execute(
                live(select(ChapterORM), ChapterORM)
                .where(ChapterORM.book_id == chapter.book_id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )
    ordered = [c for c in siblings if c.id != chapter.id]
    new_pos = min(payload.position, len(ordered))
    ordered.insert(new_pos, chapter)
    await _renumber_two_phase(db, ordered)
    await db.commit()
    return _chapter_dict(chapter, await book_cast_index(db, chapter.book_id))


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(
    chapter_id: UUID,
    scenes: Optional[str] = Query(None, pattern="^(move|delete)$"),
    to: Optional[UUID] = None,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Move a chapter to the Trash without silently orphaning its prose.

    A chapter with scenes needs `?scenes=move&to={chapter_id}` (they are appended
    to that chapter of the same book, in order) or `?scenes=delete` (they go to
    the Trash with the chapter and come back with it, SPEC-13). Neither → 409
    with the count, so the UI can ask the author; nothing changes (SPEC-04).
    """
    chapter = await _owned_chapter(chapter_id, current_user, db)
    owned = await _chapter_scenes(db, chapter.id)
    if owned and scenes is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Chapter has scenes: move them to another chapter "
                "(scenes=move&to=<chapter_id>) or delete them (scenes=delete).",
                "scene_count": len(owned),
            },
        )
    if owned and scenes == "move":
        if to is None or to == chapter.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scenes=move needs `to`: another chapter of this book",
            )
        target = await _owned_chapter(to, current_user, db)
        if target.book_id != chapter.book_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scenes can only move to a chapter of the same book",
            )
        # Read the target's scenes BEFORE re-parenting: an autoflush of the new
        # chapter_id with the old positions could collide on the UNIQUE.
        ordered = [*await _chapter_scenes(db, target.id), *owned]
        for s in owned:
            s.chapter_id = target.id
        await _renumber_two_phase(db, ordered)
    await db.flush()
    # Whatever scenes remain (scenes=delete) are trashed with the chapter.
    await trash.soft_delete(db, "chapter", chapter, created_by=current_user.id)
    await db.commit()
    return None


async def _chapter_scenes(db: AsyncSession, chapter_id: UUID) -> list:
    return list(
        (
            await db.execute(
                live(select(SceneORM), SceneORM)
                .where(SceneORM.chapter_id == chapter_id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )


# --- Book-scoped scenes -----------------------------------------------------------


@router.get("/{book_id}/scenes", response_model=dict)
async def list_book_scenes(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Every scene of this book — filed in a chapter or not — so nothing
    generated is invisible. Scenes carry their book (SPEC-04)."""
    await _owned_book(book_id, current_user, db)
    rows = (
        await db.execute(
            book_order(live(select(SceneORM, ChapterORM.title), SceneORM)).where(
                SceneORM.book_id == book_id
            )
        )
    ).all()
    scenes = [s for s, _ in rows]
    titles = {s.id: title for s, title in rows}

    jobs = await latest_jobs(db, "scene", [s.id for s in scenes])
    return {
        "scenes": [
            {
                "id": str(s.id),
                "job": jobs.get(str(s.id)),
                "title": s.title,
                "status": s.status,
                "chapter_id": str(s.chapter_id) if s.chapter_id else None,
                "chapter_title": titles[s.id],
                "characters": s.characters,
                "preview": (scene_text(s)[:200] or None),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenes
        ]
    }


# --- Generate into a chapter ------------------------------------------------------


async def book_cast_index(db: AsyncSession, book_id: UUID) -> CastIndex:
    """The book's live cast: the one load chapter responses resolve against."""
    rows = await db.execute(
        live(select(CharacterORM), CharacterORM).where(CharacterORM.book_id == book_id)
    )
    return CastIndex(rows.scalars().all())


def chapter_people(
    chapter: ChapterORM, index: CastIndex
) -> tuple[list[str], Optional[str]]:
    """(the chapter's live cast ids, its live POV id or None)."""
    chapter_cast = [
        c for c in (str(x) for x in chapter.character_ids or []) if c in index.names
    ]
    pov = str(chapter.pov_character_id) if chapter.pov_character_id else None
    return chapter_cast, pov if pov in index.names else None


def with_pov(ids: list[str], pov_id: Optional[str]) -> list[str]:
    """An explicit cast (if any) with the chapter's POV first: the one rule for
    a single scene and a batch (SPEC-32)."""
    return [pov_id, *(i for i in ids if i != pov_id)] if ids and pov_id else ids


def beat_people(
    beat: dict, index: CastIndex, chapter_cast: list[str], pov_id: Optional[str]
) -> tuple[list[str], list[str]]:
    """A planned beat's cast: the one rule for "Generate all planned scenes"
    and the beat's published `cast_ids`, which the modal sends (review W-1).

    Returns (character ids, still-unresolved secondary names). The ids are the
    beat's own cast (its live character ids plus any secondary name that now
    resolves against the book, via classify_people), else the chapter's live
    cast; the chapter's POV character is always added, first."""
    own = classify_people(beat["character_ids"] + beat["secondary_names"], index, None)
    ids = list(own.ids or chapter_cast)
    if pov_id is not None and pov_id not in ids:
        ids.insert(0, pov_id)
    return ids, own.secondary


@router.post("/chapters/{chapter_id}/scenes/generate", response_model=dict)
async def generate_scene_into_chapter(
    chapter_id: UUID,
    payload: ChapterSceneRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, max_length=200),
):
    """Prose-mode generation (one LLM call per beat) landing in the chapter.

    Two scenes in one chapter are legitimate, so duplicates are recognised by a
    client-supplied `Idempotency-Key` header (SPEC-07): a repeat while the first
    is in flight returns that scene and job instead of spending again.
    """
    chapter = await _owned_chapter(chapter_id, current_user, db)
    dedupe_key = (
        f"scene:{current_user.id}:{idempotency_key}" if idempotency_key else None
    )

    async def _existing() -> Optional[dict]:
        if dedupe_key is None:
            return None
        job = await jobs_repo.find_active(db, dedupe_key)
        if job is None:
            return None
        return {
            "scene_id": str(job.domain_id),
            "job_id": str(job.id),
            "chapter_id": str(chapter_id),
            "status": "processing",
        }

    if (found := await _existing()) is not None:
        return found

    if payload.source_id:
        owned = (
            await db.execute(
                live(select(SourceORM), SourceORM).where(
                    SourceORM.id == payload.source_id,
                    SourceORM.user_id == current_user.id,
                    # Provenance stays inside the book (book is the root).
                    SourceORM.book_id == chapter.book_id,
                )
            )
        ).scalar_one_or_none()
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
            )

    await check_user_budget(db, current_user.id)

    # A scene written from a planned beat carries the beat's id; an id the
    # chapter no longer has (a stale modal) is stored as None. The cast is the
    # request's, with the chapter POV first (with_pov, as the batch casts it).
    wanted = (payload.planned_beat_id or "").strip().lower()
    beats = normalize_planned_beats(chapter.planned_beats)
    beat = next((b for b in beats if b["node_id"] == wanted), None)
    index = await book_cast_index(db, chapter.book_id)
    chapter_cast, pov_id = chapter_people(chapter, index)
    character_ids = with_pov([str(c) for c in payload.character_ids], pov_id)
    pov_character = payload.pov_character or index.names.get(pov_id)
    secondary = beat_people(beat, index, chapter_cast, pov_id)[1] if beat else None

    # The cast is resolved against THIS book before anything is spent (SPEC-01).
    cast = await resolve_cast(
        db,
        chapter.book_id,
        character_ids=[UUID(c) for c in character_ids],
    )
    cast_names = [c.name for c in cast]
    cast_ids = [str(c.id) for c in cast]

    # Preflight with the same estimator the modal shows (SPEC-12/14, BRD R7.3):
    # refuse with the estimate rather than start a scene that cannot finish.
    canon = await build_canon(db, chapter.book_id)
    est = estimates.scene(
        canon_chars=len(canon.full_block()),
        cast_context_chars=sum(len(render_character(c)) + 1500 for c in cast),
        target_words=payload.target_word_count,
        mode=payload.mode,
        cast_size=len(cast),
    )
    assessed = await estimates.assess(db, current_user.id, est)
    if not assessed["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": assessed["reason"], "estimate": assessed},
        )

    # Plain values survive the rollback of a failed insert attempt below
    # (ORM instances get expired on rollback). The chapter summary and the prior
    # scene's tail are NOT carried in the job: they go stale across a pause, so
    # the handler recomputes them from the DB when it starts (SPEC-08).
    book_id = str(chapter.book_id)
    user_id = current_user.id

    request_dict = payload.model_dump(mode="json")
    request_dict["characters"] = cast_names
    request_dict["character_ids"] = cast_ids
    request_dict["pov_character"] = pov_character
    request_dict["planned_beat_id"] = beat["node_id"] if beat else None
    request_dict["beat_title"] = beat["title"] if beat else None
    if beat is not None:
        request_dict["secondary_names"] = secondary

    # Concurrent appends can both compute max+1; the UNIQUE constraint turns
    # that into an IntegrityError — recreate scene AND job (both roll back)
    # with a recomputed position, once.
    # Ensemble is opt-in AND tier-gated (docs/BRD.md §8): on the free tier it
    # isn't allowed, so fall back to prose rather than reject the request.
    job_kind = _scene_job_kind(payload.mode)
    for attempt in (1, 2):
        # Job + scene commit atomically: no 'processing' scene without a
        # durable job.
        try:
            # add_scene_job flushes: a position clash can raise here, not
            # only at commit.
            scene, job = await scene_batch.add_scene_job(
                db,
                chapter_id=chapter_id,
                book_id=UUID(book_id),
                user_id=user_id,
                request=request_dict,
                job_kind=job_kind,
                dedupe_key=dedupe_key,
                source_id=payload.source_id,
            )
            if job.domain_id != scene.id:  # an identical request won the race
                await db.rollback()
                return await _existing()
            job_id = str(job.id)
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            # The unique index on the idempotency key, not a position clash.
            if (found := await _existing()) is not None:
                return found
            if attempt == 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Scene position conflict; please retry",
                )
    await db.refresh(scene)

    # Say which mode actually runs: on the free tier ensemble falls back to
    # prose, and that used to happen silently (SPEC-12).
    mode_used = "ensemble" if job_kind == "generate_ensemble_scene" else "prose"
    return {
        "scene_id": str(scene.id),
        "job_id": job_id,
        "chapter_id": str(chapter_id),
        "position": scene.position,
        "status": scene.status,
        "mode_requested": payload.mode,
        "mode_used": mode_used,
        "mode_reason": (
            f"Ensemble is not available on the {get_tier().name} tier; "
            "the scene is written in prose mode."
            if payload.mode != mode_used
            else None
        ),
    }


def _scene_job_kind(mode: str) -> str:
    return (
        "generate_ensemble_scene"
        if mode == "ensemble" and get_tier().allow_ensemble
        else "generate_prose_scene"
    )


@router.post("/chapters/{chapter_id}/scenes/generate-planned", response_model=dict)
async def generate_planned_scenes(
    chapter_id: UUID,
    payload: PlannedScenesRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """One scene per planned beat of a promoted chapter, written in order.

    The beats are the chapter's `planned_beats` (SPEC-23); a "Planned beats:"
    block typed into the summary is text, not beats. A beat that already has a
    live scene in the chapter (one whose request carries the beat's id) is
    skipped, so a second click after a failure picks up where it stopped. Each
    scene takes its beat's cast (else the chapter's), setting and tone, plus the
    chapter's POV character; editing the chapter summary changes none of it.

    Same gates as a single scene: budget check, the scene estimator's preflight,
    the "llm" route class, and a dedupe key — one batch per chapter in flight, a
    repeat returns it. The batch queues the scenes that fit the rolling 24-hour
    budget now and defers the rest (`deferred`, `reason`; SPEC-34): a later
    click writes them. It is refused only when not even the first fits. The
    jobs run as a chain (app/jobs/scene_batch.py): only the first is enqueued
    now, and each completed scene enqueues the next, so every scene starts from
    the one before it.
    """
    chapter = await _owned_chapter(chapter_id, current_user, db)
    key = scene_batch.batch_key(current_user.id, chapter_id)

    async def _existing() -> Optional[dict]:
        job = await scene_batch.find_active_batch_job(db, key)
        if job is None:
            return None
        batch = (job.payload or {}).get("batch") or {}
        return {
            "chapter_id": str(chapter_id),
            "scene_id": str(job.domain_id),
            "job_id": str(job.id),
            "status": "processing",
            "already_running": True,
            "total": batch.get("total"),
            # Scenes of the batch not yet completed, this one included.
            "queued": 1 + len(batch.get("remaining") or []),
            "skipped": batch.get("skipped", 0),
            "deferred": 0,
            "reason": None,
        }

    if (found := await _existing()) is not None:
        return found

    beats = normalize_planned_beats(chapter.planned_beats)
    if not beats:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This chapter has no planned beats. Promote it from the "
            "outline, or generate scenes one at a time.",
        )
    todo = unwritten_beats(beats, await scene_batch.written_beats(db, chapter_id))
    if not todo:
        return {
            "chapter_id": str(chapter_id),
            "scene_id": None,
            "job_id": None,
            "status": "nothing_to_do",
            "total": len(beats),
            "queued": 0,
            "skipped": len(beats),
            "deferred": 0,
            "reason": None,
        }

    await check_user_budget(db, current_user.id)

    # Each beat's cast (SPEC-23): an explicit request cast for every beat
    # (resolve_cast below 422s on an unknown id, SPEC-01) with the chapter's
    # POV first (with_pov, as a single scene), else beat_people.
    index = await book_cast_index(db, chapter.book_id)
    chapter_cast, pov_id = chapter_people(chapter, index)
    explicit = with_pov([str(c) for c in payload.character_ids], pov_id)

    def beat_cast(beat: dict) -> tuple[list[str], list[str]]:
        """(the scene's character ids, its still-unresolved secondary names)."""
        ids, secondary = beat_people(beat, index, chapter_cast, pov_id)
        return explicit or ids, secondary

    plans = [(beat, *beat_cast(beat)) for beat in todo]
    # resolve_cast once per distinct cast (it also 422s an empty one).
    casts: dict[tuple, list] = {}
    for _, ids, _ in plans:
        if tuple(ids) not in casts:
            casts[tuple(ids)] = await resolve_cast(
                db, chapter.book_id, character_ids=[UUID(c) for c in ids]
            )
    pov = index.names.get(pov_id) if pov_id is not None else None

    # Each scene's estimate (SPEC-12), so a beat's own cast size counts. The
    # scenes that fit are queued and the rest deferred (SPEC-34); when none
    # fits, assessing the first refuses with its estimate.
    canon = await build_canon(db, chapter.book_id)
    canon_chars = len(canon.full_block())
    per_scene = [
        estimates.scene(
            canon_chars=canon_chars,
            cast_context_chars=sum(
                len(render_character(c)) + 1500 for c in casts[tuple(ids)]
            ),
            target_words=payload.target_word_count,
            mode=payload.mode,
            cast_size=len(casts[tuple(ids)]),
        )
        for _, ids, _ in plans
    ]
    remaining = await remaining_budget_24h(db, current_user.id)
    fit = estimates.affordable(per_scene, remaining)
    head = per_scene[: max(fit, 1)]
    est = replace(
        head[0],
        calls=sum(e.calls for e in head),
        input_tokens=sum(e.input_tokens for e in head),
        output_tokens=sum(e.output_tokens for e in head),
        notes=[*head[0].notes, f"{len(head)} scene(s), written one after another."],
    )
    assessed = await estimates.assess(db, current_user.id, est)
    if not assessed["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": assessed["reason"], "estimate": assessed},
        )
    n, deferred = fit, len(todo) - fit
    plans = plans[:n]

    requests = [
        {
            "source_id": None,
            "character_ids": [str(c.id) for c in casts[tuple(ids)]],
            "characters": [c.name for c in casts[tuple(ids)]],
            "scene_description": beat_scene_description(beat),
            "setting": payload.setting or beat.get("setting") or "",
            "emotional_tone": payload.emotional_tone or beat.get("tone") or "",
            "pov_character": pov,
            "planned_beat_id": beat["node_id"],
            "beat_title": beat["title"],
            "secondary_names": secondary,
            "target_word_count": payload.target_word_count,
            "style_notes": payload.style_notes,
            "mode": payload.mode,
        }
        for beat, ids, secondary in plans
    ]
    job_kind = _scene_job_kind(payload.mode)
    book_id = chapter.book_id
    user_id = current_user.id
    batch = {
        "key": key,
        "chapter_id": str(chapter_id),
        "kind": job_kind,
        "index": 0,
        # The chapter's planned-beat count (the API's `total`), not this chain's
        # length: beats written before the batch started are `skipped`.
        "total": len(beats),
        "skipped": len(beats) - len(todo),
        "remaining": requests[1:],
    }
    for attempt in (1, 2):
        try:
            # add_scene_job flushes: a position clash with a concurrent append
            # can raise here, not only at commit.
            scene, job = await scene_batch.add_scene_job(
                db,
                chapter_id=chapter_id,
                book_id=book_id,
                user_id=user_id,
                request=requests[0],
                job_kind=job_kind,
                dedupe_key=f"{key}:0",
                batch=batch,
            )
            if job.domain_id != scene.id:  # an identical request won the race
                await db.rollback()
                return await _existing()
            job_id = str(job.id)
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            if (found := await _existing()) is not None:
                return found
            if attempt == 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Scene position conflict; please retry",
                )
    await db.refresh(scene)

    mode_used = "ensemble" if job_kind == "generate_ensemble_scene" else "prose"
    return {
        "chapter_id": str(chapter_id),
        "scene_id": str(scene.id),
        "job_id": job_id,
        "status": scene.status,
        "total": len(beats),
        "queued": n,
        "skipped": len(beats) - len(todo),
        "deferred": deferred,
        "reason": (
            f"{n} of {len(todo)} scenes fit your budget now; generate again as "
            "your 24-hour budget frees up to write the other "
            f"{deferred} (written scenes are skipped)."
            if deferred
            else None
        ),
        "mode_requested": payload.mode,
        "mode_used": mode_used,
        "estimate": assessed,
    }


@router.post(
    "/chapters/{chapter_id}/scenes",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_scene(
    chapter_id: UUID,
    payload: ManualSceneCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a scene WITHOUT LLM generation — a blank/manual draft to write in.

    Unlike /scenes/generate this spends no model quota: it just appends an
    (optionally pre-filled) scene row the author can then edit via PUT
    /scenes/{id}/content. A scene created with content is immediately part of
    the chapter's prose (continuity/export read it), so status is 'completed'
    when content is supplied, else 'draft'."""
    chapter = await _owned_chapter(chapter_id, current_user, db)
    book_id = chapter.book_id  # plain values survive a rollback below
    user_id = current_user.id
    content = payload.content or ""
    # Append with a one-shot retry on position conflict (see create_chapter).
    for attempt in (1, 2):
        next_pos = (
            await db.execute(
                live(
                    select(func.coalesce(func.max(SceneORM.position), -1)), SceneORM
                ).where(SceneORM.chapter_id == chapter_id)
            )
        ).scalar_one() + 1
        scene = SceneORM(
            user_id=user_id,
            book_id=book_id,
            chapter_id=chapter_id,
            position=next_pos,
            title=payload.title,
            setting=payload.setting,
            emotional_tone=payload.emotional_tone,
            characters=payload.characters,
            content=content,
            word_count=len(content.split()),
            status="completed" if content.strip() else "draft",
        )
        db.add(scene)
        try:
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Scene position conflict; please retry",
                )
    await db.refresh(scene)
    return {
        "scene_id": str(scene.id),
        "chapter_id": str(chapter_id),
        "position": scene.position,
        "status": scene.status,
    }


# --- Draft editing -----------------------------------------------------------------


@router.put("/scenes/{scene_id}/content", response_model=dict)
async def update_scene_content(
    scene_id: UUID,
    payload: SceneContentUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Save an edit to the draft prose, snapshotting the previous state."""
    scene = await lock_scene(db, await owned_scene(scene_id, current_user, db))
    snapshot_draft(db, scene)
    scene.content = payload.content
    scene.word_count = len(payload.content.split())
    scene.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "id": str(scene.id),
        "word_count": scene.word_count,
        "updated_at": scene.updated_at.isoformat(),
    }


@router.patch("/scenes/{scene_id}", response_model=dict)
async def update_scene(
    scene_id: UUID,
    payload: SceneUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a scene (SPEC-37). Titles are not versioned."""
    scene = await owned_scene(scene_id, current_user, db)
    scene.title = payload.title
    await db.commit()
    return {"id": str(scene.id), "title": scene.title}


@router.get("/scenes/{scene_id}/revisions", response_model=dict)
async def list_scene_revisions(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    scene = await owned_scene(
        scene_id, current_user, db, selectinload(SceneORM.revisions)
    )
    return {
        "scene_id": str(scene.id),
        "revisions": [
            {
                "id": str(r.id),
                "word_count": r.word_count,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "content": r.content,
            }
            for r in scene.revisions
            # AI proposals (SPEC-19) live behind /proposals with their own
            # status and stale-draft checks; they are not restorable history.
            if r.source != "ai_revise"
        ],
    }


@router.patch("/scenes/{scene_id}/position", response_model=dict)
async def move_scene(
    scene_id: UUID,
    payload: PositionUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder a scene within its chapter."""
    scene = await owned_scene(scene_id, current_user, db)
    if scene.chapter_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found in a chapter",
        )
    siblings = (
        (
            await db.execute(
                live(select(SceneORM), SceneORM)
                .where(SceneORM.chapter_id == scene.chapter_id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )
    ordered = [s for s in siblings if s.id != scene.id]
    new_pos = min(payload.position, len(ordered))
    ordered.insert(new_pos, scene)
    await _renumber_two_phase(db, ordered)
    await db.commit()
    return {"id": str(scene.id), "position": scene.position}


# --- Export ---------------------------------------------------------------------


@router.get("/{book_id}/export")
async def export_book(
    book_id: UUID,
    format: str = "md",
    include_synopsis: bool = False,
    skip_empty: bool = True,
    from_chapter: Optional[int] = Query(None, ge=1),
    to_chapter: Optional[int] = Query(None, ge=1),
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the book's current draft state as md | docx | epub.

    The synopsis is the author's storyboard, not manuscript prose, so it is left
    out unless `include_synopsis=true`. Chapters without prose are left out
    unless `skip_empty=false`. `from_chapter`/`to_chapter` pick an inclusive,
    1-based range of book positions; headings keep the book's numbering, so a
    range starting at 3 opens on "Chapter 3" (SPEC-21).
    """
    if format not in CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown format '{format}' (md | docx | epub)",
        )
    if (
        from_chapter is not None
        and to_chapter is not None
        and from_chapter > to_chapter
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_chapter must not be after to_chapter",
        )
    book = await _owned_book(book_id, current_user, db)
    chapters = (
        (
            await db.execute(
                live(select(ChapterORM), ChapterORM)
                .options(selectinload(ChapterORM.scenes))
                .where(ChapterORM.book_id == book.id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )

    export = BookExport(
        title=book.title,
        author=book.author or "",
        synopsis=(book.synopsis or "") if include_synopsis else "",
        chapters=select_chapters(
            [
                ChapterExport(
                    title=c.title,
                    summary=c.summary or "",
                    scenes=[
                        SceneExport(title=s.title or "", content=scene_text(s))
                        for s in sorted(c.scenes, key=lambda s: s.position)
                        if scene_text(s) and s.deleted_at is None
                    ],
                )
                for c in chapters
            ],
            skip_empty=skip_empty,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
        ),
    )

    filename_stem = (
        "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in book.title)
        .strip()
        .replace(" ", "-")
        .lower()
        or "book"
    )

    if format == "md":
        body: bytes = to_markdown(export).encode()
    elif format == "docx":
        body = to_docx(export)
    else:
        body = to_epub(export)

    return Response(
        content=body,
        media_type=CONTENT_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename_stem}.{format}"'
        },
    )
