"""Resolve a requested cast against a book (SPEC-01).

A scene's cast used to be free-text names; a name that matched no character
silently produced voice-blind prose. Every generation entrypoint now resolves
the request to Character rows of THIS book and rejects anything else, naming
the unknowns, before any job or quota is spent.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import Character
from app.versioning.trash import live


async def resolve_cast(
    db: AsyncSession,
    book_id: UUID,
    *,
    character_ids: list[UUID] | None = None,
) -> list[Character]:
    """The requested cast as Character rows of this book, in request order.

    The cast is by ID only (the deprecated by-name input was removed, SPEC-15c).
    Raises 422 listing every unknown, and 422 when the result is empty."""
    # A trashed character is not in the cast (SPEC-13).
    query = live(select(Character), Character).where(Character.book_id == book_id)
    rows = (await db.execute(query)).scalars().all()
    by_id = {str(c.id): c for c in rows}
    resolved: list[Character] = []
    unknown: list[str] = []

    for cid in character_ids or []:
        c = by_id.get(str(cid))
        (resolved.append(c) if c else unknown.append(str(cid)))

    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "These characters are not in this book's cast. Add "
                "them in the Canon tab first, or pick from the cast.",
                "unknown": unknown,
            },
        )
    deduped: list[Character] = []
    for c in resolved:
        if c not in deduped:
            deduped.append(c)
    if not deduped:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Pick at least one character from the cast."},
        )
    return deduped
