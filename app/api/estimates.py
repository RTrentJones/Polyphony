"""POST /estimate — what an action will cost before the author clicks (SPEC-12).

The enqueue endpoints preflight with the same estimators (app/core/estimates.py),
so the number shown is the number enforced (docs/BRD.md R7.5).
"""

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import estimates
from app.core.database import get_db
from app.core.orm_models import Book, Source, User as UserORM
from app.core.security import get_current_active_user

router = APIRouter()


class EstimateRequest(BaseModel):
    action: Literal["scene", "outline", "continuity", "extraction"]
    book_id: Optional[UUID] = None
    source_id: Optional[UUID] = None
    chapter_id: Optional[UUID] = None
    character_ids: list[UUID] = Field(default_factory=list)
    target_word_count: int = Field(default=800, ge=100, le=3000)
    mode: Literal["prose", "ensemble", "staged", "single"] = "prose"
    max_chapters: Optional[int] = Field(default=None, ge=1)


async def _owned_book(db: AsyncSession, book_id: Optional[UUID], user: UserORM) -> Book:
    book = None
    if book_id is not None:
        book = (
            await db.execute(
                select(Book).where(Book.id == book_id, Book.user_id == user.id)
            )
        ).scalar_one_or_none()
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


@router.post("/estimate", response_model=dict)
async def estimate(
    payload: EstimateRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Calls, tokens, dollars and today's remaining allowance for one action."""
    from app.planning.canon import build_canon

    if payload.action == "extraction":
        source = (
            await db.execute(
                select(Source).where(
                    Source.id == payload.source_id, Source.user_id == current_user.id
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source not found"
            )
        text = source.content_text or ""
        if payload.max_chapters:
            from app.parsing.extraction_plan import first_chapters

            text = first_chapters(text, payload.max_chapters)
        est = estimates.extraction(text=text)
        return await estimates.assess(db, current_user.id, est)

    book = await _owned_book(db, payload.book_id, current_user)
    canon = await build_canon(db, book.id)
    canon_chars = len(canon.full_block())

    if payload.action == "scene":
        ids = {str(i) for i in payload.character_ids}
        cast = [c for c in canon.characters if str(c.id) in ids] or canon.characters[:2]
        from app.planning.canon import render_character

        cast_chars = sum(len(render_character(c)) + 1500 for c in cast)
        est = estimates.scene(
            canon_chars=canon_chars,
            cast_context_chars=cast_chars,
            target_words=payload.target_word_count,
            mode="ensemble" if payload.mode == "ensemble" else "prose",
            cast_size=len(cast),
        )
    elif payload.action == "outline":
        est = estimates.outline(
            canon_chars=canon_chars, staged=payload.mode != "single"
        )
    else:
        from app.api.plans import _collect_prose
        from app.planning.continuity import build_fact_sheet

        prose = await _collect_prose(book.id, payload.chapter_id, db)
        sheet = await build_fact_sheet(db, book.id)
        est = estimates.continuity(
            fact_sheet_chars=len(sheet), prose_words=len(prose.split())
        )
    return await estimates.assess(db, current_user.id, est)
