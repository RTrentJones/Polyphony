"""Version history + restore for canon entities (docs/ADR-002 §5).

Read the history of any versioned entity and restore an earlier state. Restore is
FORWARD-ONLY: it re-applies old content to the live row AND appends a new version
(reason='restored_from:N'), so max(version_no) still equals the live row and
nothing is ever deleted. Old content is re-validated through the SAME validator
the write path uses — an old shape today's validator rejects returns 409 rather
than corrupting the live row.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    BookPlan as BookPlanORM,
    CanonEntry as CanonEntryORM,
    Chapter as ChapterORM,
    Character as CharacterORM,
    Source as SourceORM,
    StyleGuide as StyleGuideORM,
    User as UserORM,
)
from app.characters.serialize import CHARACTER_FIELDS
from app.core.security import get_current_active_user
from app.planning.outline import validate_outline_nodes
from app.planning.planned_beat import (
    LEGACY_MARKER,
    beat_from_line,
    beat_scene_description,
    normalize_planned_beats,
    split_legacy_summary,
)
from app.versioning import entities as entity_versions
from app.versioning import repository as versions_repo
from app.versioning import trash

router = APIRouter()

# Simple field-set entities (name -> ORM, restorable fields). book_plan restores
# specially (its content is re-validated), so it is handled outside this table.
_CHARACTER_FIELDS = CHARACTER_FIELDS
_CANON_FIELDS = ("name", "category", "content", "position")
_STYLE_FIELDS = ("pov", "tense", "tone", "comps", "sample_prose")
_FIELD_ENTITIES = {
    "character": (CharacterORM, _CHARACTER_FIELDS),
    "canon_entry": (CanonEntryORM, _CANON_FIELDS),
    "style_guide": (StyleGuideORM, _STYLE_FIELDS),
    # SPEC-13: sources and chapters are versioned like the rest of the canon.
    "source": (SourceORM, entity_versions.SOURCE_FIELDS),
    "chapter": (ChapterORM, entity_versions.CHAPTER_RESTORE_FIELDS),
}
# Entities whose version content is wider than what restore writes back.
_SNAPSHOT_CONTENT = {
    "source": entity_versions.source_content,
    "chapter": entity_versions.chapter_content,
}
# synopsis is a field on Book (entity_id == book_id); book_plan restores specially.
_ENTITY_TYPES = {"book_plan", "synopsis", *_FIELD_ENTITIES}


async def _owned_book(
    book_id: UUID, current_user: UserORM, db: AsyncSession
) -> BookORM:
    book = (
        await db.execute(
            select(BookORM).where(
                BookORM.id == book_id, BookORM.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


def _check_entity_type(entity_type: str) -> None:
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_type '{entity_type}'",
        )


@router.get("/books/{book_id}/versions/{entity_type}/{entity_id}", response_model=dict)
async def list_entity_versions(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Version history for one entity, newest first (max version_no = live)."""
    await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    versions = await versions_repo.list_versions(db, entity_type, entity_id)
    # book_id on the row is the tenant guard — never surface another book's log.
    versions = [v for v in versions if v.book_id == book_id]
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "versions": [
            {
                "version_no": v.version_no,
                "reason": v.reason,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "created_by": str(v.created_by) if v.created_by else None,
            }
            for v in versions
        ],
    }


@router.get(
    "/books/{book_id}/versions/{entity_type}/{entity_id}/{version_no}",
    response_model=dict,
)
async def get_entity_version(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    version_no: int,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Full content of one version."""
    await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    version = await versions_repo.get_version(db, entity_type, entity_id, version_no)
    if version is None or version.book_id != book_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )
    return {
        "version_no": version.version_no,
        "reason": version.reason,
        "content": version.content,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


@router.post(
    "/books/{book_id}/versions/{entity_type}/{entity_id}/restore/{version_no}",
    response_model=dict,
)
async def restore_entity_version(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    version_no: int,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore an entity to an earlier version (forward-only append)."""
    book = await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    version = await versions_repo.get_version(db, entity_type, entity_id, version_no)
    if version is None or version.book_id != book_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )

    reason = f"restored_from:{version_no}"
    reextract = None

    if entity_type == "book_plan":
        plan = (
            await db.execute(
                select(BookPlanORM).where(
                    BookPlanORM.id == entity_id, BookPlanORM.book_id == book_id
                )
                # Locked, so a generation committing "generating" first is seen.
                .with_for_update()
            )
        ).scalar_one_or_none()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        if plan.status in ("generating", "paused"):
            # The running job writes its nodes over the plan when it finishes.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This plan is being generated; restore it when that finishes.",
            )
        payload = version.content or {}
        nodes = payload.get("content") if isinstance(payload, dict) else payload
        try:
            # Re-validate through the write path's validator — an old shape the
            # current validator rejects must 409, never corrupt the live row.
            validated = validate_outline_nodes(nodes)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Version {version_no} is not restorable: {e}",
            )
        plan.content = validated
        snapshot_content = {"kind": plan.kind, "content": validated}
        live_id = plan.id
    elif entity_type == "synopsis":
        # The synopsis lives on Book (entity_id == book_id); restore sets it.
        content = version.content or {}
        book.synopsis = content.get("synopsis")
        snapshot_content = {"synopsis": book.synopsis}
        live_id = book.id
    else:  # field-set entities: character | canon_entry | style_guide | ...
        model, fields = _FIELD_ENTITIES[entity_type]
        row = (
            await db.execute(
                select(model).where(model.id == entity_id, model.book_id == book_id)
            )
        ).scalar_one_or_none()
        content = version.content or {}
        if row is not None and getattr(row, "deleted_at", None) is not None:
            # Restoring a version of a trashed entity brings it out of the
            # Trash first, with everything it owned (SPEC-13).
            try:
                await trash.restore(db, entity_type, row, created_by=current_user.id)
            except trash.TrashConflict as e:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=e.detail
                )
        prior_text = getattr(row, "content_text", None) if row is not None else None
        if row is None:
            # RECREATE a purged entity, preserving its identity (PR review #4).
            # The (book_id, name) uniqueness check still applies (409 below).
            extra = (
                {"user_id": book.user_id}
                if entity_type in ("character", "source")
                else {}
            )
            if entity_type == "chapter":
                extra = {"position": await _next_chapter_position(db, book_id)}
            row = model(id=entity_id, book_id=book_id, **extra)
            for field_name in fields:
                if field_name in content:
                    setattr(row, field_name, content[field_name])
            if entity_type == "chapter":
                row.plan_node_id = await _free_plan_node(
                    db, book_id, content.get("plan_node_id")
                )
            db.add(row)
        else:
            for field_name in fields:
                if field_name in content:
                    setattr(row, field_name, content[field_name])
        if entity_type == "chapter":
            # The promoted cast/POV (SPEC-10), against the book's current cast.
            await entity_versions.apply_chapter_cast(db, row, content)
            if "planned_beats" not in content:
                _legacy_beats_to_data(row)
        if entity_type == "source":
            row.word_count = len((row.content_text or "").split())
            if row.content_text != prior_text:
                # Restoring text does NOT re-extract on its own: extraction
                # spends quota and proposes canon, so the author chooses.
                reextract = {
                    "offered": True,
                    "endpoint": f"/api/v1/sources/{row.id}/process",
                    "message": "The source text changed. Re-run extraction to "
                    "review canon from the restored text.",
                }
        await _check_name_free(db, entity_type, row)
        snapshot_fn = _SNAPSHOT_CONTENT.get(entity_type)
        snapshot_content = (
            snapshot_fn(row) if snapshot_fn else {f: getattr(row, f) for f in fields}
        )
        live_id = row.id

    await versions_repo.snapshot(
        db,
        book_id=book.id,
        entity_type=entity_type,
        entity_id=live_id,
        content=snapshot_content,
        reason=reason,
        created_by=current_user.id,
    )
    try:
        await db.commit()
    except IntegrityError:
        # e.g. restoring a character name that now collides under (book_id, name).
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Restore conflicts with the current state (name already in use)",
        )
    result = {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "restored_from": version_no,
    }
    if reextract:
        result["reextract"] = reextract
    return result


def _legacy_beats_to_data(chapter) -> None:
    """A chapter version from before SPEC-23 restores a "Planned beats:" block
    into the summary: convert it at this write, as migration 0019 did. The
    block leaves the summary only when its lines become the planned beats: the
    chapter has none and converting loses no author text. Otherwise (live
    beats already there, more than 12 lines, a "- : x" line, a cut) the block
    stays in the summary as text, so no author text leaves a live field
    (review NB-2)."""
    legacy = split_legacy_summary(chapter.summary)
    if legacy is None or normalize_planned_beats(chapter.planned_beats):
        return
    beats = normalize_planned_beats([beat_from_line(ln.text) for ln in legacy.lines])
    if not keeps_every_word(chapter.summary, legacy.text, beats):
        return
    chapter.summary = legacy.text
    chapter.planned_beats = beats


def _words(text: str) -> list[str]:
    return sorted(w for w in text.replace(":", " ").split() if w != "-")


def keeps_every_word(summary: str, text: str, beats: list[dict]) -> bool:
    """The summary left after the block plus the beats hold every word the
    block had (bullets, colons and spacing aside): converting loses no author
    text. Migration 0019 carries an inline copy (parity-tested)."""
    described = (beat_scene_description(b) for b in beats)
    return _words(summary) == _words(" ".join([text, LEGACY_MARKER, *described]))


async def _next_chapter_position(db: AsyncSession, book_id: UUID) -> int:
    return (
        await db.execute(
            trash.live(
                select(func.coalesce(func.max(ChapterORM.position), -1)), ChapterORM
            ).where(ChapterORM.book_id == book_id)
        )
    ).scalar_one() + 1


async def _free_plan_node(db: AsyncSession, book_id: UUID, plan_node_id) -> UUID | None:
    """A recreated chapter keeps its outline-node link only while no live
    chapter holds that node (one live chapter per node, SPEC-10): the node may
    have been promoted again since the chapter was purged. Otherwise it comes
    back unlinked rather than colliding with the live one."""
    if not plan_node_id:
        return None
    try:
        node_id = UUID(str(plan_node_id))
    except ValueError:
        return None
    with db.no_autoflush:
        taken = (
            await db.execute(
                trash.live(select(ChapterORM.id), ChapterORM)
                .where(
                    ChapterORM.book_id == book_id,
                    ChapterORM.plan_node_id == node_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return None if taken is not None else node_id


async def _check_name_free(db: AsyncSession, entity_type: str, row) -> None:
    """409 naming the live entity that already holds this name (SPEC-13)."""
    if entity_type not in ("character", "canon_entry"):
        return
    model = _FIELD_ENTITIES[entity_type][0]
    with db.no_autoflush:
        taken = (
            await db.execute(
                trash.live(select(model), model).where(
                    model.book_id == row.book_id,
                    model.name == row.name,
                    model.id != row.id,
                )
            )
        ).scalar_one_or_none()
    if taken is not None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"'{row.name}' is already in use in this book. "
                "Rename or delete it, then restore.",
                "conflict": {
                    "type": entity_type,
                    "id": str(taken.id),
                    "name": taken.name,
                },
            },
        )
