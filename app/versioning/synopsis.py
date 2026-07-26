"""Centralized synopsis mutation — the synopsis is Canon and fully versioned.

Every path that changes a book's synopsis (create, edit, import) MUST go through
`record_synopsis`, so the invariant holds: create -> v1, edit -> v2, and the
latest version always equals the live value (docs/ADR-002 §5, PR review #2). A
book created with synopsis A and edited to B keeps A as v1 — nothing is lost.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.versioning import repository as versions_repo

_ENTITY = "synopsis"


async def record_synopsis(
    db: AsyncSession,
    book,
    new_value: str | None,
    *,
    reason: str,
    created_by: UUID | None = None,
) -> None:
    """Set book.synopsis and append a version. Does NOT commit (caller owns txn).

    If the book already has an UNVERSIONED synopsis (created before this path, or
    seeded without a version), that prior value is preserved as v1 before the new
    value is written — so pre-existing data is never lost on the first overwrite.
    """
    existing = await versions_repo.latest(db, _ENTITY, book.id)
    if existing is None and book.synopsis:
        # Preserve the pre-existing live synopsis as v1 before overwriting it.
        await versions_repo.snapshot(
            db,
            book_id=book.id,
            entity_type=_ENTITY,
            entity_id=book.id,
            content={"synopsis": book.synopsis},
            reason="created",
            created_by=created_by,
        )
    book.synopsis = new_value
    await versions_repo.snapshot(
        db,
        book_id=book.id,
        entity_type=_ENTITY,
        entity_id=book.id,
        content={"synopsis": new_value},
        reason=reason,
        created_by=created_by,
    )
