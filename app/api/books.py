"""Book, chapter, and draft endpoints: the book-writing workflow."""

from datetime import datetime, timezone
from typing import Optional
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
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.characters.cast import resolve_cast
from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    Chapter as ChapterORM,
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
    to_docx,
    to_epub,
    to_markdown,
)
from app.jobs import repository as jobs_repo
from app.llm.tier import get_tier
from app.versioning.scenes import snapshot_before_delete
from app.versioning.synopsis import record_synopsis

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


class PositionUpdate(BaseModel):
    position: int = Field(..., ge=0)


class ChapterSceneRequest(BaseModel):
    source_id: Optional[UUID] = None  # provenance only
    # The cast, as character IDs of this book (SPEC-01). `characters` (names) is
    # the deprecated input, resolved against the book's cast by name/alias; an
    # unknown name is rejected rather than silently generated voice-blind.
    character_ids: list[UUID] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    scene_description: str = Field(..., min_length=10)
    setting: str
    emotional_tone: str
    pov_character: Optional[str] = None
    target_word_count: int = Field(default=800, ge=100, le=3000)
    style_notes: Optional[str] = None
    # Generation mode (docs/BRD.md §8). prose = one call per beat (default);
    # ensemble = narrator/character/editor loop, opt-in and tier-gated.
    mode: str = Field(default="prose", pattern="^(prose|ensemble)$")


class SceneContentUpdate(BaseModel):
    content: str = Field(..., min_length=1)


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
            select(ChapterORM)
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
            select(SceneORM)
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


async def _renumber_two_phase(db: AsyncSession, ordered: list) -> None:
    """Renumber rows to 0..n-1 under an immediate UNIQUE (parent, position).

    Phase 1 parks every row on a negative temp position — the API validates
    client positions >= 0, so negatives can never collide with real data —
    then phase 2 lands the final contiguous order. Caller commits.
    """
    for idx, item in enumerate(ordered):
        item.position = -(idx + 1)
    await db.flush()
    for idx, item in enumerate(ordered):
        item.position = idx


def _chapter_dict(c: ChapterORM) -> dict:
    return {
        "id": str(c.id),
        "book_id": str(c.book_id),
        "position": c.position,
        "title": c.title,
        "summary": c.summary,
        "status": c.status,
        # From the outline node it was promoted from (SPEC-10): scene defaults.
        "plan_node_id": str(c.plan_node_id) if c.plan_node_id else None,
        "pov_character_id": str(c.pov_character_id) if c.pov_character_id else None,
        "character_ids": c.character_ids or [],
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
    return {
        "books": [
            {
                "id": str(b.id),
                "title": b.title,
                "author": b.author,
                "genre": b.genre,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
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
                select(ChapterORM)
                .where(ChapterORM.book_id == book.id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": str(book.id),
        "title": book.title,
        "author": book.author,
        "synopsis": book.synopsis,
        "genre": book.genre,
        "status": book.status,
        "chapters": [_chapter_dict(c) for c in chapters],
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
    if payload.position is None:
        # Append: concurrent appends can both compute max+1; the UNIQUE
        # (book_id, position) constraint turns that into an IntegrityError —
        # recompute and retry once instead of silently duplicating.
        for attempt in (1, 2):
            max_pos = (
                await db.execute(
                    select(func.coalesce(func.max(ChapterORM.position), -1)).where(
                        ChapterORM.book_id == book_id
                    )
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
                    select(ChapterORM)
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
        await db.commit()
    await db.refresh(chapter)
    return _chapter_dict(chapter)


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
                select(SceneORM)
                .where(SceneORM.chapter_id == chapter.id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )
    from app.api.jobs import latest_jobs

    jobs = await latest_jobs(db, "scene", [s.id for s in scenes])
    return {
        **_chapter_dict(chapter),
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


@router.patch("/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    chapter_id: UUID,
    payload: ChapterUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    for field_name in ("title", "summary", "status"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(chapter, field_name, value)
    await db.commit()
    return _chapter_dict(chapter)


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
                select(ChapterORM)
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
    return _chapter_dict(chapter)


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(
    chapter_id: UUID,
    scenes: Optional[str] = Query(None, pattern="^(move|delete)$"),
    to: Optional[UUID] = None,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chapter without silently orphaning its prose (SPEC-04).

    A chapter with scenes needs `?scenes=move&to={chapter_id}` (they are appended
    to that chapter of the same book, in order) or `?scenes=delete` (each scene's
    prose is kept in entity_versions first). Neither → 409 with the count, so the
    UI can ask the author; nothing changes.
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
    elif owned:
        for s in owned:
            await snapshot_before_delete(db, s, created_by=current_user.id)
            await db.delete(s)
    await db.flush()
    await db.delete(chapter)
    await db.commit()
    return None


async def _chapter_scenes(db: AsyncSession, chapter_id: UUID) -> list:
    return list(
        (
            await db.execute(
                select(SceneORM)
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
    scenes = (
        (
            await db.execute(
                select(SceneORM)
                .where(SceneORM.book_id == book_id)
                .order_by(SceneORM.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    # Chapter titles for the filed scenes (one lookup), so the UI can group them.
    ch_ids = {s.chapter_id for s in scenes if s.chapter_id}
    titles: dict = {}
    if ch_ids:
        rows = (
            await db.execute(
                select(ChapterORM.id, ChapterORM.title).where(ChapterORM.id.in_(ch_ids))
            )
        ).all()
        titles = {cid: title for cid, title in rows}

    from app.api.jobs import latest_jobs

    jobs = await latest_jobs(db, "scene", [s.id for s in scenes])
    return {
        "scenes": [
            {
                "id": str(s.id),
                "job": jobs.get(str(s.id)),
                "title": s.title,
                "status": s.status,
                "chapter_id": str(s.chapter_id) if s.chapter_id else None,
                "chapter_title": titles.get(s.chapter_id),
                "characters": s.characters,
                "preview": (scene_text(s)[:200] or None),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenes
        ]
    }


# --- Generate into a chapter ------------------------------------------------------


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
                select(SourceORM).where(
                    SourceORM.id == payload.source_id,
                    SourceORM.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
            )

    await check_user_budget(db, current_user.id)

    # The cast is resolved against THIS book before anything is spent (SPEC-01).
    cast = await resolve_cast(
        db,
        chapter.book_id,
        character_ids=payload.character_ids,
        names=payload.characters,
    )
    cast_names = [c.name for c in cast]
    cast_ids = [str(c.id) for c in cast]

    # Plain values survive the rollback of a failed insert attempt below
    # (ORM instances get expired on rollback). The chapter summary and the prior
    # scene's tail are NOT carried in the job: they go stale across a pause, so
    # the handler recomputes them from the DB when it starts (SPEC-08).
    book_id = str(chapter.book_id)

    request_dict = payload.model_dump(mode="json")
    request_dict["characters"] = cast_names
    request_dict["character_ids"] = cast_ids

    # Concurrent appends can both compute max+1; the UNIQUE constraint turns
    # that into an IntegrityError — recreate scene AND job (both roll back)
    # with a recomputed position, once.
    for attempt in (1, 2):
        next_pos = (
            await db.execute(
                select(func.coalesce(func.max(SceneORM.position), -1)).where(
                    SceneORM.chapter_id == chapter_id
                )
            )
        ).scalar_one() + 1
        scene = SceneORM(
            user_id=current_user.id,
            book_id=UUID(book_id),
            source_id=payload.source_id,
            chapter_id=chapter_id,
            position=next_pos,
            setting=payload.setting,
            emotional_tone=payload.emotional_tone,
            characters=cast_names,
            character_ids=cast_ids,
            scene_description=payload.scene_description,
            scene_request=request_dict,
            status="processing",
        )
        db.add(scene)
        await db.flush()
        # Ensemble is opt-in AND tier-gated (docs/BRD.md §8): on the free tier it
        # isn't allowed, so fall back to prose rather than reject the request.
        job_kind = (
            "generate_ensemble_scene"
            if payload.mode == "ensemble" and get_tier().allow_ensemble
            else "generate_prose_scene"
        )
        # Job + scene commit atomically: no 'processing' scene without a
        # durable job.
        job = await jobs_repo.enqueue(
            db,
            kind=job_kind,
            payload={
                "scene_id": str(scene.id),
                "request": request_dict,
                "user_id": str(current_user.id),
                "book_id": book_id,
            },
            user_id=current_user.id,
            max_attempts=1,  # a retry re-spends LLM budget; user can re-trigger
            dedupe_key=dedupe_key,
            domain=("scene", scene.id),
        )
        if job.domain_id != scene.id:  # an identical request won the race
            await db.rollback()
            return await _existing()
        job_id = str(job.id)
        try:
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

    return {
        "scene_id": str(scene.id),
        "job_id": job_id,
        "chapter_id": str(chapter_id),
        "position": scene.position,
        "status": scene.status,
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
    book_id = chapter.book_id  # a plain value survives a rollback below
    content = payload.content or ""
    # Append with a one-shot retry on position conflict (see create_chapter).
    for attempt in (1, 2):
        next_pos = (
            await db.execute(
                select(func.coalesce(func.max(SceneORM.position), -1)).where(
                    SceneORM.chapter_id == chapter_id
                )
            )
        ).scalar_one() + 1
        scene = SceneORM(
            user_id=current_user.id,
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
    scene = await owned_scene(scene_id, current_user, db)

    previous = scene_text(scene)
    if previous:
        db.add(
            SceneRevisionORM(
                scene_id=scene.id,
                content=previous,
                word_count=len(previous.split()),
                source="edited" if scene.content else "generated",
            )
        )
    scene.content = payload.content
    scene.word_count = len(payload.content.split())
    scene.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "id": str(scene.id),
        "word_count": scene.word_count,
        "updated_at": scene.updated_at.isoformat(),
    }


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
                select(SceneORM)
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
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the book's current draft state as md | docx | epub.

    The synopsis is the author's storyboard, not manuscript prose, so it is left
    out unless `include_synopsis=true`.
    """
    if format not in CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown format '{format}' (md | docx | epub)",
        )
    book = await _owned_book(book_id, current_user, db)
    chapters = (
        (
            await db.execute(
                select(ChapterORM)
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
        chapters=[
            ChapterExport(
                title=c.title,
                summary=c.summary or "",
                scenes=[
                    SceneExport(title=s.title or "", content=scene_text(s))
                    for s in sorted(c.scenes, key=lambda s: s.position)
                    if scene_text(s)
                ],
            )
            for c in chapters
        ],
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
