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
# author's back. The promoted cast (`pov_character_id`, `character_ids`, SPEC-10)
# is recorded and restored, but through `apply_chapter_cast`, which drops
# characters no longer live in the book. `plan_node_id` is recorded too; it never
# changes on a live chapter, so only recreating a purged chapter writes it back
# (and only while no live chapter holds that node — see the restore endpoint).
CHAPTER_FIELDS = (
    "title",
    "summary",
    "status",
    "position",
    "plan_node_id",
    "pov_character_id",
    "character_ids",
)
CHAPTER_RESTORE_FIELDS = ("title", "summary", "status")
CHAPTER_CAST_FIELDS = ("pov_character_id", "character_ids")


def source_content(source) -> dict:
    return {f: getattr(source, f, None) for f in SOURCE_FIELDS}


def _id_str(value) -> str | None:
    return str(value) if value is not None else None


def chapter_content(chapter) -> dict:
    """A chapter's version content. JSON-safe: ids are stored as strings."""
    content = {f: getattr(chapter, f, None) for f in CHAPTER_FIELDS}
    content["plan_node_id"] = _id_str(content["plan_node_id"])
    content["pov_character_id"] = _id_str(content["pov_character_id"])
    content["character_ids"] = [str(c) for c in (content["character_ids"] or [])]
    return content


async def apply_chapter_cast(db: AsyncSession, chapter, content: dict) -> None:
    """Write a version's cast/POV back onto a chapter, against the CURRENT cast.

    A character trashed or purged since the version was taken is dropped rather
    than dangled (the same rule promote applies, SPEC-10). A version taken before
    the cast was versioned has neither key and leaves the live cast alone.
    Does NOT commit.
    """
    if not any(f in content for f in CHAPTER_CAST_FIELDS):
        return
    from sqlalchemy import select

    from app.core.orm_models import Character

    with db.no_autoflush:
        live_ids = {
            str(cid)
            for cid in (
                await db.execute(
                    select(Character.id).where(
                        Character.book_id == chapter.book_id,
                        Character.deleted_at.is_(None),
                    )
                )
            ).scalars()
        }
    if "character_ids" in content:
        ids: list[str] = []
        for cid in content.get("character_ids") or []:
            if str(cid) in live_ids and str(cid) not in ids:
                ids.append(str(cid))
        chapter.character_ids = ids
    if "pov_character_id" in content:
        pov = content.get("pov_character_id")
        chapter.pov_character_id = (
            UUID(str(pov)) if pov and str(pov) in live_ids else None
        )


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
