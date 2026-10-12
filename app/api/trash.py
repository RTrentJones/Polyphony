"""A book's Trash: list, restore and purge soft-deleted entities (SPEC-13).

Deleting a character, canon entry, source, chapter or scene moves it here
(app/versioning/trash.py). Restore brings it back with everything it owned —
a character's voice, a chapter's scenes. Purge is permanent and separate; the
UI asks for the item's name typed out before calling it.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.orm_models import Book as BookORM, User as UserORM
from app.core.security import get_current_active_user
from app.versioning import trash

router = APIRouter()


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


async def _trashed(book_id: UUID, entity_type: str, entity_id: UUID, db):
    if entity_type not in trash.MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown type '{entity_type}' "
            f"(one of {', '.join(sorted(trash.MODELS))})",
        )
    row = await trash.trashed_row(db, entity_type, entity_id, book_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not in this book's Trash"
        )
    return row


@router.get("/books/{book_id}/trash", response_model=dict)
async def list_trash(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-deleted entities of this book, newest first."""
    await _owned_book(book_id, current_user, db)
    return {"items": await trash.list_trash(db, book_id)}


@router.post(
    "/books/{book_id}/trash/{entity_type}/{entity_id}/restore", response_model=dict
)
async def restore_from_trash(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a trashed entity. 409 (nothing changed) names what it collides
    with — a live entity holding its name, or a chapter still in the Trash."""
    await _owned_book(book_id, current_user, db)
    row = await _trashed(book_id, entity_type, entity_id, db)
    try:
        await trash.restore(db, entity_type, row, created_by=current_user.id)
    except trash.TrashConflict as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.detail)
    name = trash.display_name(entity_type, row)
    await db.commit()
    return {"type": entity_type, "id": str(entity_id), "name": name, "restored": True}


@router.delete(
    "/books/{book_id}/trash/{entity_type}/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def purge_from_trash(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a trashed entity permanently (only from the Trash, never a live
    one). Its version history stays."""
    await _owned_book(book_id, current_user, db)
    row = await _trashed(book_id, entity_type, entity_id, db)
    await trash.purge(db, entity_type, row)
    await db.commit()
    return None
