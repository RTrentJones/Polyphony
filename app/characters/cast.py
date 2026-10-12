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


def _forms(c: Character) -> list[str]:
    forms = [c.name]
    forms.extend(a for a in (getattr(c, "aliases", None) or []) if a)
    return [f.strip().lower() for f in forms if f and f.strip()]


async def resolve_cast(
    db: AsyncSession,
    book_id: UUID,
    *,
    character_ids: list[UUID] | None = None,
    names: list[str] | None = None,
) -> list[Character]:
    """The requested cast as Character rows of this book, in request order.

    IDs win; names (deprecated input) resolve by exact name, then
    case-insensitively by name or alias. Raises 422 listing every unknown, and
    422 when the result is empty."""
    query = select(Character).where(Character.book_id == book_id)
    if hasattr(Character, "deleted_at"):
        query = query.where(Character.deleted_at.is_(None))
    rows = (await db.execute(query)).scalars().all()
    by_id = {str(c.id): c for c in rows}
    resolved: list[Character] = []
    unknown: list[str] = []

    for cid in character_ids or []:
        c = by_id.get(str(cid))
        (resolved.append(c) if c else unknown.append(str(cid)))

    if not character_ids:
        by_exact = {c.name: c for c in rows}
        by_form: dict[str, Character] = {}
        for c in rows:
            for f in _forms(c):
                by_form.setdefault(f, c)
        for n in names or []:
            c = by_exact.get(n) or by_form.get((n or "").strip().lower())
            (resolved.append(c) if c else unknown.append(n))

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
