"""Soft delete, restore and purge: the Trash (SPEC-13, closes F16).

Invariant 3 says nothing the author wrote is destroyed. Deleting a character,
canon entry, source, chapter or scene therefore only sets `deleted_at` (after
appending a `deleted` version), and the row waits in its book's Trash until it
is restored or explicitly purged.

Two rules keep a trashed row out of everything else:

* `live()` is THE filter. Every list, cast, prompt, export and continuity query
  goes through it (directly or through an `_owned_*` helper / `build_canon`), so
  "is it deleted?" is answered in one place.
* Uniqueness holds over live rows only (partial indexes, migration 0015), so a
  trashed name can be reused, and restoring into a taken one is a 409 naming
  the conflict rather than an IntegrityError.

A character's voice chunks stay put while it is trashed: retrieval is scoped by
character id and a trashed character is never cast, so the voice is unreachable
until the character comes back, and restore brings it back intact. Purge
removes chunks and row in ONE transaction.

Every function here takes the caller's session and NEVER commits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.characters.serialize import character_snapshot
from app.core.orm_models import (
    CanonEntry,
    Chapter,
    Character,
    ExtractionRun,
    Job,
    Scene,
    Source,
)
from app.jobs import repository as jobs_repo
from app.versioning import repository as versions_repo
from app.versioning.entities import chapter_content, source_content
from app.versioning.scenes import snapshot_before_delete

MODELS = {
    "character": Character,
    "canon_entry": CanonEntry,
    "source": Source,
    "chapter": Chapter,
    "scene": Scene,
}


class TrashConflict(Exception):
    """Restoring would break a live invariant (a taken name, a trashed parent).
    `detail` is the API's 409 body: a message plus the conflicting entity."""

    def __init__(self, message: str, conflict: dict | None = None):
        super().__init__(message)
        self.detail = {"message": message, "conflict": conflict}


def live(query, *models):
    """Exclude soft-deleted rows of each model from a query."""
    for model in models:
        query = query.where(model.deleted_at.is_(None))
    return query


def _now() -> datetime:
    return datetime.now(timezone.utc)


def display_name(entity_type: str, row) -> str:
    if entity_type == "scene":
        return row.title or f"Scene {row.position + 1}"
    if entity_type in ("source", "chapter"):
        return row.title
    return row.name


def _content(entity_type: str, row) -> dict:
    if entity_type == "character":
        return character_snapshot(row)
    if entity_type == "canon_entry":
        return {
            "name": row.name,
            "category": row.category,
            "content": row.content,
            "position": row.position,
        }
    if entity_type == "source":
        return source_content(row)
    return chapter_content(row)


async def _version(db, entity_type, row, reason, created_by) -> None:
    if entity_type == "scene":
        # A scene's version carries its draft AND revision history (SPEC-04).
        await snapshot_before_delete(db, row, reason=reason, created_by=created_by)
        return
    await versions_repo.snapshot(
        db,
        book_id=row.book_id,
        entity_type=entity_type,
        entity_id=row.id,
        content=_content(entity_type, row),
        reason=reason,
        created_by=created_by,
    )


async def renumber_two_phase(db: AsyncSession, ordered: list) -> None:
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


async def _live_chapters(db, book_id) -> list:
    return list(
        (
            await db.execute(
                live(select(Chapter), Chapter)
                .where(Chapter.book_id == book_id)
                .order_by(Chapter.position)
            )
        )
        .scalars()
        .all()
    )


async def _live_scenes(db, chapter_id) -> list:
    return list(
        (
            await db.execute(
                live(select(Scene), Scene)
                .where(Scene.chapter_id == chapter_id)
                .order_by(Scene.position)
            )
        )
        .scalars()
        .all()
    )


# --- delete -----------------------------------------------------------------------


async def _cancel_jobs(db: AsyncSession, domain_type: str, ids: list) -> None:
    """Cancel in-flight jobs whose domain row just went to the Trash (SPEC-07):
    a queued or paused job is cancelled now, in this transaction; a running one
    is flagged and stops at its next seam between model calls (the worker then
    runs its on_cancel hook). For a job cancelled here, the domain row is moved
    out of its in-flight status in the same transaction, so a restore never
    brings back a scene or extraction that says "processing" with no job behind
    it."""
    if not ids:
        return
    jobs = (
        (
            await db.execute(
                select(Job).where(
                    Job.domain_type == domain_type,
                    Job.domain_id.in_(ids),
                    Job.status.in_(jobs_repo.ACTIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        if job.status == "running":
            job.cancel_requested = True
            continue
        await jobs_repo.mark_cancelled(db, job)
        model, in_flight = _IN_FLIGHT.get(domain_type, (None, ()))
        if model is not None:
            row = await db.get(model, job.domain_id)
            if row is not None and row.status in in_flight:
                row.status = "cancelled"


# Domain rows a trashed job's cancellation must settle, and the statuses that
# mean "a job is working on this".
_IN_FLIGHT = {
    "scene": (Scene, ("processing", "paused")),
    "extraction_run": (ExtractionRun, ("pending", "processing", "paused")),
}


async def _cancel_jobs_of(db: AsyncSession, entity_type: str, row, scenes) -> None:
    if entity_type == "scene":
        await _cancel_jobs(db, "scene", [row.id])
    elif entity_type == "chapter":
        await _cancel_jobs(db, "scene", [s.id for s in scenes])
    elif entity_type == "source":
        runs = (
            await db.execute(
                select(ExtractionRun.id).where(ExtractionRun.source_id == row.id)
            )
        ).scalars()
        await _cancel_jobs(db, "extraction_run", list(runs))


async def soft_delete(
    db: AsyncSession, entity_type: str, row, *, created_by: UUID | None = None
) -> None:
    """Move a live row to the Trash: `deleted` version first, then deleted_at.

    A chapter takes its live scenes with it (same timestamp, so restore brings
    back exactly those); its siblings close the gap, as do a scene's. Jobs
    working on what was trashed (a scene's generation, a source's extraction)
    are cancelled.
    """
    when = _now()
    await _version(db, entity_type, row, "deleted", created_by)
    scenes = await _live_scenes(db, row.id) if entity_type == "chapter" else []
    for scene in scenes:
        await _version(db, "scene", scene, "deleted", created_by)
        scene.deleted_at = when
    await _cancel_jobs_of(db, entity_type, row, scenes)
    row.deleted_at = when
    await db.flush()
    if entity_type == "chapter":
        await renumber_two_phase(db, await _live_chapters(db, row.book_id))
    elif entity_type == "scene" and row.chapter_id is not None:
        await renumber_two_phase(db, await _live_scenes(db, row.chapter_id))


# --- restore ----------------------------------------------------------------------


async def _check_conflicts(db: AsyncSession, entity_type: str, row) -> None:
    model = MODELS[entity_type]
    if entity_type in ("character", "canon_entry"):
        taken = (
            await db.execute(
                live(select(model), model).where(
                    model.book_id == row.book_id,
                    model.name == row.name,
                    model.id != row.id,
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            label = "character" if entity_type == "character" else "canon entry"
            raise TrashConflict(
                f"A {label} named '{row.name}' already exists in this book. "
                "Rename or delete it, then restore.",
                {"type": entity_type, "id": str(taken.id), "name": taken.name},
            )
    elif entity_type == "source" and row.content_hash:
        taken = (
            await db.execute(
                live(select(Source), Source).where(
                    Source.book_id == row.book_id,
                    Source.content_hash == row.content_hash,
                    Source.id != row.id,
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise TrashConflict(
                f"The source '{taken.title}' already has this content.",
                {"type": "source", "id": str(taken.id), "name": taken.title},
            )
    elif entity_type == "scene" and row.chapter_id is not None:
        chapter = await db.get(Chapter, row.chapter_id)
        if chapter is not None and chapter.deleted_at is not None:
            raise TrashConflict(
                f"This scene's chapter '{chapter.title}' is in the Trash. "
                "Restore the chapter first.",
                {"type": "chapter", "id": str(chapter.id), "name": chapter.title},
            )


async def restore(
    db: AsyncSession, entity_type: str, row, *, created_by: UUID | None = None
) -> None:
    """Bring a trashed row back with everything it owned; append `restored`.

    Raises TrashConflict (nothing changed) when restoring would collide.
    A chapter and a scene go back to their old place, or the end if the book
    has fewer live siblings now; a chapter's co-deleted scenes come with it.
    """
    await _check_conflicts(db, entity_type, row)
    deleted_at = row.deleted_at
    # Read the live siblings and co-deleted scenes BEFORE the row turns live:
    # an autoflush of it on its old position could collide on the UNIQUE.
    siblings: list | None = None
    scenes: list = []
    if entity_type == "chapter":
        siblings = await _live_chapters(db, row.book_id)
        scenes = list(
            (
                await db.execute(
                    select(Scene).where(
                        Scene.chapter_id == row.id, Scene.deleted_at == deleted_at
                    )
                )
            )
            .scalars()
            .all()
        )
    elif entity_type == "scene" and row.chapter_id is not None:
        siblings = await _live_scenes(db, row.chapter_id)
    row.deleted_at = None
    if siblings is not None:
        siblings.insert(min(row.position, len(siblings)), row)
        # Phase 1 parks the row on a negative slot in the same UPDATE that
        # makes it live, so it never holds its old position.
        await renumber_two_phase(db, siblings)
    for scene in scenes:
        scene.deleted_at = None
        await _version(db, "scene", scene, "restored", created_by)
    await db.flush()
    await _version(db, entity_type, row, "restored", created_by)


# --- purge ------------------------------------------------------------------------


async def purge(db: AsyncSession, entity_type: str, row) -> None:
    """Delete a trashed row permanently, with what it owns, in the caller's
    transaction. A character's voice chunks go in the SAME transaction as the
    row (F20: deleting them through the chunk store's own session committed
    separately). Its versions stay in entity_versions, which lives as long as
    the book."""
    if entity_type == "character" and db.bind.dialect.name == "postgresql":
        # voice_chunks is outside the ORM (pgvector); the FK would cascade too,
        # but an explicit delete keeps the order obvious and testable.
        await db.execute(
            text("DELETE FROM voice_chunks WHERE character_id = :id"),
            {"id": row.id},
        )
    elif entity_type == "chapter":
        for scene in (
            (
                await db.execute(
                    select(Scene).where(
                        Scene.chapter_id == row.id,
                        Scene.deleted_at == row.deleted_at,
                    )
                )
            )
            .scalars()
            .all()
        ):
            await db.delete(scene)
    await db.delete(row)
    await db.flush()


# --- listing ----------------------------------------------------------------------


async def _voice_samples(db: AsyncSession, character_ids: list) -> dict:
    if not character_ids or db.bind.dialect.name != "postgresql":
        return {}
    rows = await db.execute(
        text(
            "SELECT character_id, COUNT(*) AS n FROM voice_chunks "
            "WHERE character_id = ANY(:ids) GROUP BY character_id"
        ),
        {"ids": list(character_ids)},
    )
    return {r.character_id: r.n for r in rows}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def list_trash(db: AsyncSession, book_id: UUID) -> list[dict]:
    """Everything trashed in a book, newest first, with what restoring it brings
    back. A scene trashed with its chapter is listed under the chapter only."""
    items: list[dict] = []
    trashed = {}
    for entity_type, model in MODELS.items():
        trashed[entity_type] = list(
            (
                await db.execute(
                    select(model).where(
                        model.book_id == book_id, model.deleted_at.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    trashed_chapters = {c.id: c for c in trashed["chapter"]}
    scene_counts: dict = {}
    for s in trashed["scene"]:
        ch = trashed_chapters.get(s.chapter_id)
        if ch is not None and s.deleted_at == ch.deleted_at:
            scene_counts[ch.id] = scene_counts.get(ch.id, 0) + 1
    voices = await _voice_samples(db, [c.id for c in trashed["character"]])

    for entity_type, rows in trashed.items():
        for row in rows:
            restores: dict = {}
            if entity_type == "character":
                n = voices.get(row.id)
                if n is None:  # sqlite has no voice store; the counter is close
                    n = row.dialogue_count or 0
                restores = {"voice_samples": n}
                summary = f"the character bible and {_plural(n, 'voice sample')}"
            elif entity_type == "canon_entry":
                summary = f"the {row.category} entry"
            elif entity_type == "source":
                restores = {"words": row.word_count or 0}
                summary = f"{_plural(row.word_count or 0, 'word')} of source text"
            elif entity_type == "chapter":
                n = scene_counts.get(row.id, 0)
                restores = {"scenes": n}
                summary = f"the chapter and {_plural(n, 'scene')}"
            else:
                if row.chapter_id in trashed_chapters:
                    continue  # comes back with (or after) its chapter
                restores = {"words": row.word_count or 0}
                summary = f"{_plural(row.word_count or 0, 'word')} of prose"
            items.append(
                {
                    "type": entity_type,
                    "id": str(row.id),
                    "name": display_name(entity_type, row),
                    "deleted_at": row.deleted_at.isoformat(),
                    "restores": restores,
                    "summary": summary,
                }
            )
    items.sort(key=lambda i: i["deleted_at"], reverse=True)
    return items


async def trashed_row(db: AsyncSession, entity_type: str, entity_id, book_id):
    """The trashed row of this type in this book, or None."""
    model = MODELS[entity_type]
    return (
        await db.execute(
            select(model).where(
                model.id == entity_id,
                model.book_id == book_id,
                model.deleted_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
