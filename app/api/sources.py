"""Source management endpoints.

A Source is any raw input text attached to a book — an uploaded file or pasted
text (was `Manuscript`, docs/ADR-002-book-as-root.md §2). You upload INTO a
book: the book is the root of every concept, so a Source is always book-scoped
(`book_id` NOT NULL). Uploading without naming a book auto-creates one, so the
single-source-per-book flow stays one step.
"""

import hashlib

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.models import SourceStatus
from app.core.orm_models import (
    Book as BookORM,
    Character as CharacterORM,
    ExtractionRun as ExtractionRunORM,
    Source as SourceORM,
    User as UserORM,
    source_characters,
)
from app.core.security import get_current_active_user
from app.parsing.extraction_service import enqueue_extraction
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
        status=SourceStatus.COMPLETED.value,  # the file is stored + parsed
    )
    db.add(source)
    await db.flush()
    # Extraction PROPOSES; the author approves before any canon is written
    # (docs/BRD.md R4.4, PR review #1). Upload starts an extraction run, never a
    # direct write — canon is created only via the reviewed commit.
    run = await enqueue_extraction(
        db, book_id=book.id, source_id=source.id, user_id=current_user.id
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
        "extraction_run_id": str(run.id),
        "message": "Source uploaded. Extracting canon for your review.",
    }


class PasteSourceRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content_text: str = Field(..., min_length=1)
    author: Optional[str] = None
    book_id: Optional[UUID] = None


class SourceEditRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    content_text: Optional[str] = Field(None, min_length=1)


def _source_response(source: SourceORM, run_id: Optional[str], verb: str) -> dict:
    msg = (
        f"Source {verb}. Extracting canon for your review."
        if run_id
        else f"Source {verb}."
    )
    return {
        "id": str(source.id),
        "book_id": str(source.book_id),
        "title": source.title,
        "author": source.author,
        "word_count": source.word_count,
        "status": source.status,
        "extraction_run_id": run_id,
        "message": msg,
    }


@router.post("/paste", response_model=dict)
async def paste_source(
    payload: PasteSourceRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a source from PASTED text (kind='paste') and start extraction.

    Pasted text is untrusted material like an upload, so it runs the same
    reviewed-commit flow — nothing reaches canon until the author approves
    (docs/BRD.md R4.4). This is how a book created directly (no file) gets source
    material to extract characters/canon from.
    """
    await check_user_budget(db, current_user.id)
    text = payload.content_text
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    word_count = len(text.split())
    book = await _resolve_book(payload.book_id, payload.title, current_user, db)

    # Duplicate guard, per BOOK (a global unique would leak a cross-tenant oracle).
    existing = await db.execute(
        select(SourceORM).where(
            SourceORM.book_id == book.id,
            SourceORM.content_hash == content_hash,
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
        kind="paste",
        title=payload.title,
        author=payload.author or None,
        content_hash=content_hash,
        content_text=text,
        word_count=word_count,
        status=SourceStatus.COMPLETED.value,
    )
    db.add(source)
    await db.flush()
    run = await enqueue_extraction(
        db, book_id=book.id, source_id=source.id, user_id=current_user.id
    )
    await db.commit()
    await db.refresh(source)
    return _source_response(source, str(run.id), "added")


@router.patch("/{source_id}", response_model=dict)
async def edit_source(
    source_id: UUID,
    payload: SourceEditRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a source's title and/or content. Changing the content RE-RUNS
    extraction (fresh proposals for review) — canon is never overwritten in place;
    the author re-approves. Lets an author fix or expand pasted material."""
    source = await _owned_source(source_id, current_user, db)
    run_id: Optional[str] = None
    if payload.title is not None:
        source.title = payload.title
    if payload.content_text is not None and payload.content_text != (
        source.content_text or ""
    ):
        await check_user_budget(db, current_user.id)
        source.content_text = payload.content_text
        source.content_hash = hashlib.sha256(
            payload.content_text.encode("utf-8")
        ).hexdigest()
        source.word_count = len(payload.content_text.split())
        run = await enqueue_extraction(
            db, book_id=source.book_id, source_id=source.id, user_id=current_user.id
        )
        run_id = str(run.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another source in this book already has this content",
        )
    await db.refresh(source)
    return _source_response(source, run_id, "updated")


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

    # Latest extraction run per source (one query), so the list can surface a
    # pending/ready review to resume instead of stranding it.
    latest_by_source: dict = {}
    src_ids = [s.id for s in sources]
    if src_ids:
        runs = (
            (
                await db.execute(
                    select(ExtractionRunORM)
                    .where(ExtractionRunORM.source_id.in_(src_ids))
                    .order_by(ExtractionRunORM.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for r in runs:
            latest_by_source.setdefault(r.source_id, r)  # first seen = newest

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
                "latest_extraction": (
                    {
                        "id": str(latest_by_source[s.id].id),
                        "status": latest_by_source[s.id].status,
                    }
                    if s.id in latest_by_source
                    else None
                ),
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
    """Get source details, incl. the latest extraction run so a review that was
    navigated away from is recoverable (PR review #3). The source is marked
    `completed` on upload, so without surfacing the run the pending proposals would
    be stranded and re-uploading would double-spend extraction."""
    source = await _owned_source(source_id, current_user, db)
    latest_run = (
        await db.execute(
            select(ExtractionRunORM)
            .where(ExtractionRunORM.source_id == source_id)
            .order_by(ExtractionRunORM.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "id": str(source.id),
        "book_id": str(source.book_id),
        "kind": source.kind,
        "title": source.title,
        "author": source.author,
        "word_count": source.word_count,
        "status": source.status,
        # The stored text so a paste source can be viewed/edited in place.
        "content_text": source.content_text,
        "uploaded_at": (source.uploaded_at.isoformat() if source.uploaded_at else None),
        "processed_at": (
            source.processed_at.isoformat() if source.processed_at else None
        ),
        "latest_extraction": (
            {"id": str(latest_run.id), "status": latest_run.status}
            if latest_run
            else None
        ),
    }


@router.get("/{source_id}/characters", response_model=dict)
async def get_source_characters(
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the characters this source contributed to (its cast).

    Resolved through the `source_characters` association, NOT `Character.source_id`
    (which is single-valued and goes NULL when the file is deleted). A character
    re-proposed and merged from this source has source_id=None yet is genuinely
    part of the cast; querying the association returns it (PR review #2).
    """
    await _owned_source(source_id, current_user, db)

    characters_result = await db.execute(
        select(CharacterORM)
        .join(
            source_characters,
            source_characters.c.character_id == CharacterORM.id,
        )
        .where(source_characters.c.source_id == source_id)
        .order_by(CharacterORM.name)
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
    """Re-run extraction — proposes canon again for review, never a direct write."""
    source = await _owned_source(source_id, current_user, db)
    if not source.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source has no stored content to process",
        )
    await check_user_budget(db, current_user.id)
    run = await enqueue_extraction(
        db, book_id=source.book_id, source_id=source.id, user_id=current_user.id
    )
    await db.commit()
    return {
        "id": str(source.id),
        "extraction_run_id": str(run.id),
        "status": run.status,
    }
