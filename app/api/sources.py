"""Source management endpoints.

A Source is any raw input text attached to a book — an uploaded file or pasted
text (was `Manuscript`, docs/ADR-002-book-as-root.md §2). You upload INTO a
book: the book is the root of every concept, so a Source is always book-scoped
(`book_id` NOT NULL). Uploading without naming a book auto-creates one, so the
single-source-per-book flow stays one step.
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.models import SourceStatus
from app.core.orm_models import (
    Book as BookORM,
    Character as CharacterORM,
    Source as SourceORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.jobs import repository as jobs_repo
from app.parsing.pipeline import (
    UploadValidationError,
    save_upload,
)

router = APIRouter()


async def _resolve_book(
    book_id: Optional[UUID],
    title: str,
    current_user: UserORM,
    db: AsyncSession,
) -> BookORM:
    """The book a new Source lands in: an existing owned book, or a fresh one.

    Book is the root (docs/ADR-002-book-as-root.md §1), so a Source cannot be
    parentless. When the caller doesn't name a book we create one titled after
    the upload — the common 'just give me a book from this file' path.
    """
    if book_id is not None:
        book = (
            await db.execute(
                select(BookORM).where(
                    BookORM.id == book_id,
                    BookORM.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if book is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
            )
        return book

    book = BookORM(user_id=current_user.id, title=title or "Untitled")
    db.add(book)
    await db.flush()
    return book


@router.post("/upload", response_model=dict)
async def upload_source(
    file: UploadFile = File(...),
    title: str = "",
    author: str = "",
    book_id: Optional[UUID] = None,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a source file; character extraction/indexing runs in the background."""
    # Extraction spends LLM quota — gate it on the per-user daily budget.
    await check_user_budget(db, current_user.id)
    if not title:
        title = file.filename or "Untitled"

    try:
        saved = await save_upload(file.filename or "upload.txt", await file.read())
    except UploadValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error parsing document: {e}",
        )

    book = await _resolve_book(book_id, title, current_user, db)

    # Duplicate guard: content_hash is unique per BOOK (a global unique would
    # leak a cross-tenant existence oracle — migration 0003).
    existing = await db.execute(
        select(SourceORM).where(
            SourceORM.book_id == book.id,
            SourceORM.content_hash == saved["content_hash"],
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This source is already in this book",
        )

    source = SourceORM(
        book_id=book.id,
        user_id=current_user.id,
        kind="upload",
        title=title,
        author=author or None,
        content_hash=saved["content_hash"],
        content_text=saved["text"],
        word_count=saved["word_count"],
        status=SourceStatus.PROCESSING.value,
    )
    db.add(source)
    await db.flush()
    # Job + source row commit atomically; the pipeline reads the durable
    # content_text from the row, so the payload is just the ids.
    await jobs_repo.enqueue(
        db,
        kind="process_source",
        payload={
            "source_id": str(source.id),
            "user_id": str(current_user.id),
        },
        user_id=current_user.id,
        max_attempts=2,  # extraction is cheap and reprocess-idempotent
    )
    await db.commit()
    await db.refresh(source)

    return {
        "id": str(source.id),
        "book_id": str(source.book_id),
        "title": source.title,
        "author": source.author,
        "word_count": source.word_count,
        "status": source.status,
        "message": "Source uploaded successfully. Processing started.",
    }


@router.get("/", response_model=dict)
async def list_sources(
    book_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 20,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's sources (optionally filtered to one book)."""
    where = [SourceORM.user_id == current_user.id]
    if book_id is not None:
        where.append(SourceORM.book_id == book_id)

    total = (
        await db.execute(select(func.count()).select_from(SourceORM).where(*where))
    ).scalar_one()

    result = await db.execute(
        select(SourceORM)
        .where(*where)
        .order_by(SourceORM.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
    )
    sources = result.scalars().all()

    return {
        "sources": [
            {
                "id": str(s.id),
                "book_id": str(s.book_id),
                "kind": s.kind,
                "title": s.title,
                "author": s.author,
                "word_count": s.word_count,
                "status": s.status,
                "uploaded_at": s.uploaded_at.isoformat() if s.uploaded_at else None,
                "processed_at": s.processed_at.isoformat() if s.processed_at else None,
            }
            for s in sources
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def _owned_source(
    source_id: UUID, current_user: UserORM, db: AsyncSession
) -> SourceORM:
    result = await db.execute(
        select(SourceORM).where(
            SourceORM.id == source_id,
            SourceORM.user_id == current_user.id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
        )
    return source


@router.get("/{source_id}", response_model=dict)
async def get_source(
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get source details."""
    source = await _owned_source(source_id, current_user, db)
    return {
        "id": str(source.id),
        "book_id": str(source.book_id),
        "kind": source.kind,
        "title": source.title,
        "author": source.author,
        "word_count": source.word_count,
        "status": source.status,
        "uploaded_at": (source.uploaded_at.isoformat() if source.uploaded_at else None),
        "processed_at": (
            source.processed_at.isoformat() if source.processed_at else None
        ),
    }


@router.get("/{source_id}/characters", response_model=dict)
async def get_source_characters(
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List characters extracted from a source."""
    await _owned_source(source_id, current_user, db)

    characters_result = await db.execute(
        select(CharacterORM).where(CharacterORM.source_id == source_id)
    )
    characters = characters_result.scalars().all()

    return {
        "source_id": str(source_id),
        "characters": [
            {
                "id": str(c.id),
                "book_id": str(c.book_id),
                "name": c.name,
                "description": c.description,
                "dialogue_count": c.dialogue_count,
                "indexed_at": c.indexed_at.isoformat() if c.indexed_at else None,
            }
            for c in characters
        ],
    }


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a source. Its extracted characters SURVIVE — they are Canon now.

    `Character.source_id` is provenance only (ON DELETE SET NULL): the file was
    just how the cast arrived, and deleting it must never delete the cast
    (docs/ADR-002-book-as-root.md §2). To remove a character, delete the
    character.
    """
    source = await _owned_source(source_id, current_user, db)
    await db.delete(source)
    await db.commit()
    return None


@router.post("/{source_id}/process", response_model=dict)
async def reprocess_source(
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run character extraction/indexing (e.g. after a failed run)."""
    source = await _owned_source(source_id, current_user, db)
    if not source.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source has no stored content to process",
        )
    await check_user_budget(db, current_user.id)
    source.status = SourceStatus.PROCESSING.value
    await jobs_repo.enqueue(
        db,
        kind="process_source",
        payload={
            "source_id": str(source.id),
            "user_id": str(current_user.id),
        },
        user_id=current_user.id,
        max_attempts=2,
    )
    await db.commit()
    return {"id": str(source.id), "status": source.status}
