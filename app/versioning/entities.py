"""Snapshots for sources and chapters (SPEC-13, F16).

Source content and chapter titles/summaries used to be edited in place with no
history. They are versioned like the rest of the canon (ADR-002 §5): create
appends v1, every edit appends the new state, restore appends forward. Every
path that creates or edits one of them calls `record`, before committing, so
the version lands in the same transaction as the write it records.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.versioning import repository as versions_repo

# What a version carries, and (for the restore endpoint) what it writes back.
SOURCE_FIELDS = ("title", "kind", "content_text", "content_hash")
# `position` is recorded but not restored: it is the book's order, not the
# chapter's content, and restoring it would reorder its siblings behind the
# author's back.
CHAPTER_FIELDS = ("title", "summary", "status", "position")
CHAPTER_RESTORE_FIELDS = ("title", "summary", "status")


def source_content(source) -> dict:
    return {f: getattr(source, f, None) for f in SOURCE_FIELDS}


def chapter_content(chapter) -> dict:
    return {f: getattr(chapter, f, None) for f in CHAPTER_FIELDS}


CONTENT = {"source": source_content, "chapter": chapter_content}


async def ensure_baseline(
    db: AsyncSession, entity_type: str, row, *, created_by: UUID | None = None
) -> None:
    """Before the first versioned edit of a row created before SPEC-13, keep its
    current state as v1 so the edit never overwrites unversioned text. Call
    BEFORE mutating the row. Does NOT commit."""
    if await versions_repo.latest(db, entity_type, row.id) is None:
        await record(db, entity_type, row, reason="created", created_by=created_by)


async def record(
    db: AsyncSession,
    entity_type: str,
    row,
    *,
    reason: str,
    created_by: UUID | None = None,
) -> None:
    """Append the row's current state as a version. Does NOT commit."""
    await db.flush()  # a new row needs its id
    await versions_repo.snapshot(
        db,
        book_id=row.book_id,
        entity_type=entity_type,
        entity_id=row.id,
        content=CONTENT[entity_type](row),
        reason=reason,
        created_by=created_by,
    )
