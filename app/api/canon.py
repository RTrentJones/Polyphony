"""Canon entities API: worldbuilding entries + the book's style guide (Phase 3).

Book-scoped CRUD. Every write is versioned (docs/ADR-002 §5) via the Phase 2
entity_versions log, so canon edits are never clobbered and are restorable
through /books/{id}/versions/{entity_type}/{entity_id}.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    CanonEntry as CanonEntryORM,
    StyleGuide as StyleGuideORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.versioning import repository as versions_repo

router = APIRouter()

CATEGORIES = {"world", "location", "faction", "item", "concept", "org"}


class CanonEntryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    category: str = "concept"
    content: Optional[str] = None
    position: int = 0


class CanonEntryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[str] = None
    content: Optional[str] = None
    position: Optional[int] = None


class StyleGuideUpsert(BaseModel):
    pov: Optional[str] = Field(None, max_length=50)
    tense: Optional[str] = Field(None, max_length=20)
    tone: Optional[str] = None
    comps: Optional[str] = None
    sample_prose: Optional[str] = None


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


def _entry_content(e: CanonEntryORM) -> dict:
    return {
        "name": e.name,
        "category": e.category,
        "content": e.content,
        "position": e.position,
    }


def _entry_dict(e: CanonEntryORM) -> dict:
    return {"id": str(e.id), **_entry_content(e)}


def _style_content(s: StyleGuideORM) -> dict:
    return {
        "pov": s.pov,
        "tense": s.tense,
        "tone": s.tone,
        "comps": s.comps,
        "sample_prose": s.sample_prose,
    }


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"category must be one of {sorted(CATEGORIES)}",
        )


@router.get("/books/{book_id}/canon", response_model=dict)
async def get_canon(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The book's Canon surface: entries (ordered) + the style guide."""
    await _owned_book(book_id, current_user, db)
    entries = (
        (
            await db.execute(
                select(CanonEntryORM)
                .where(CanonEntryORM.book_id == book_id)
                .order_by(CanonEntryORM.position, CanonEntryORM.name)
            )
        )
        .scalars()
        .all()
    )
    style = (
        await db.execute(select(StyleGuideORM).where(StyleGuideORM.book_id == book_id))
    ).scalar_one_or_none()
    return {
        "entries": [_entry_dict(e) for e in entries],
        "style": ({"id": str(style.id), **_style_content(style)} if style else None),
    }


@router.post(
    "/books/{book_id}/canon/entries",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_canon_entry(
    book_id: UUID,
    payload: CanonEntryCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    _validate_category(payload.category)
    entry = CanonEntryORM(
        book_id=book.id,
        name=payload.name,
        category=payload.category,
        content=payload.content,
        position=payload.position,
    )
    db.add(entry)
    try:
        await db.flush()
        await versions_repo.snapshot(
            db,
            book_id=book.id,
            entity_type="canon_entry",
            entity_id=entry.id,
            content=_entry_content(entry),
            reason="created",
            created_by=current_user.id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A canon entry with this name already exists in this book",
        )
    await db.refresh(entry)
    return _entry_dict(entry)


async def _owned_entry(
    book_id: UUID, entry_id: UUID, current_user: UserORM, db: AsyncSession
) -> CanonEntryORM:
    await _owned_book(book_id, current_user, db)
    entry = (
        await db.execute(
            select(CanonEntryORM).where(
                CanonEntryORM.id == entry_id, CanonEntryORM.book_id == book_id
            )
        )
    ).scalar_one_or_none()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Canon entry not found"
        )
    return entry


@router.patch("/books/{book_id}/canon/entries/{entry_id}", response_model=dict)
async def update_canon_entry(
    book_id: UUID,
    entry_id: UUID,
    payload: CanonEntryUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await _owned_entry(book_id, entry_id, current_user, db)
    if payload.category is not None:
        _validate_category(payload.category)
    for field_name in ("name", "category", "content", "position"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(entry, field_name, value)
    await versions_repo.snapshot(
        db,
        book_id=book_id,
        entity_type="canon_entry",
        entity_id=entry.id,
        content=_entry_content(entry),
        reason="edited",
        created_by=current_user.id,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A canon entry with this name already exists in this book",
        )
    return _entry_dict(entry)


@router.delete(
    "/books/{book_id}/canon/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_canon_entry(
    book_id: UUID,
    entry_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await _owned_entry(book_id, entry_id, current_user, db)
    await db.delete(entry)
    await db.commit()
    return None


@router.put("/books/{book_id}/canon/style", response_model=dict)
async def upsert_style_guide(
    book_id: UUID,
    payload: StyleGuideUpsert,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the book's single style guide (versioned)."""
    book = await _owned_book(book_id, current_user, db)
    style = (
        await db.execute(select(StyleGuideORM).where(StyleGuideORM.book_id == book_id))
    ).scalar_one_or_none()
    if style is None:
        style = StyleGuideORM(book_id=book.id)
        db.add(style)
        reason = "created"
    else:
        reason = "edited"
    for field_name in ("pov", "tense", "tone", "comps", "sample_prose"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(style, field_name, value)
    await db.flush()
    await versions_repo.snapshot(
        db,
        book_id=book.id,
        entity_type="style_guide",
        entity_id=style.id,
        content=_style_content(style),
        reason=reason,
        created_by=current_user.id,
    )
    await db.commit()
    await db.refresh(style)
    return {"id": str(style.id), **_style_content(style)}
