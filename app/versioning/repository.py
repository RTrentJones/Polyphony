"""Persistence for the append-only entity-version log (docs/ADR-002 §5).

Contract (mirrors app/jobs/repository.py): every function takes an explicit
AsyncSession and NEVER commits — the caller owns the transaction, so a snapshot
commits atomically with the write it records. That is the point: "regeneration
never clobbers" is only true if the version and the new live state land together.

INVARIANT (see EntityVersion docstring — it DIVERGES from SceneRevision):
append-only INCLUDING head, so max(version_no) always equals the live row.
`snapshot()` is called with the NEW state on every create/edit/generate/restore.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import EntityVersion


async def snapshot(
    session: AsyncSession,
    *,
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    content: dict | list,
    reason: str,
    created_by: UUID | None = None,
) -> EntityVersion:
    """Append a full-snapshot version for an entity. Does NOT commit.

    version_no = coalesce(max, 0) + 1. The UNIQUE (entity_type, entity_id,
    version_no) constraint is the real guard against a concurrent racer computing
    the same next number; the insert runs inside a SAVEPOINT so a conflict rolls
    back only this version (not the caller's write) and we recompute once.
    """
    for attempt in (1, 2):
        next_no = (
            await session.execute(
                select(func.coalesce(func.max(EntityVersion.version_no), 0)).where(
                    EntityVersion.entity_type == entity_type,
                    EntityVersion.entity_id == entity_id,
                )
            )
        ).scalar_one() + 1
        version = EntityVersion(
            book_id=book_id,
            entity_type=entity_type,
            entity_id=entity_id,
            version_no=next_no,
            content=content,
            reason=reason,
            created_by=created_by,
        )
        try:
            async with session.begin_nested():  # SAVEPOINT
                session.add(version)
                await session.flush()
            return version
        except IntegrityError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


async def list_versions(
    session: AsyncSession, entity_type: str, entity_id: UUID
) -> list[EntityVersion]:
    """All versions for an entity, newest first (max version_no = live state)."""
    return list(
        (
            await session.execute(
                select(EntityVersion)
                .where(
                    EntityVersion.entity_type == entity_type,
                    EntityVersion.entity_id == entity_id,
                )
                .order_by(EntityVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_version(
    session: AsyncSession, entity_type: str, entity_id: UUID, version_no: int
) -> EntityVersion | None:
    return (
        await session.execute(
            select(EntityVersion).where(
                EntityVersion.entity_type == entity_type,
                EntityVersion.entity_id == entity_id,
                EntityVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()


async def latest(
    session: AsyncSession, entity_type: str, entity_id: UUID
) -> EntityVersion | None:
    return (
        await session.execute(
            select(EntityVersion)
            .where(
                EntityVersion.entity_type == entity_type,
                EntityVersion.entity_id == entity_id,
            )
            .order_by(EntityVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
