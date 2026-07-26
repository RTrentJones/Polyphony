"""Extraction API: Source -> proposed Canon -> review -> commit (Phase 6).

Extraction proposes; the author reviews and edits; commit writes the real
entities plus an 'imported' version (docs/BRD.md R6). Book-scoped throughout.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.characters.serialize import character_snapshot
from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    CanonEntry as CanonEntryORM,
    Character as CharacterORM,
    ExtractionRun as ExtractionRunORM,
    Source as SourceORM,
    StyleGuide as StyleGuideORM,
    User as UserORM,
    source_characters,
)
from app.core.security import get_current_active_user
from app.jobs import repository as jobs_repo
from app.parsing.extraction_service import enqueue_extraction
from app.versioning import repository as versions_repo
from app.versioning.synopsis import record_synopsis

router = APIRouter()

CATEGORIES = {"world", "location", "faction", "item", "concept", "org"}


class CharacterProposal(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    role: Optional[str] = None
    description: Optional[str] = None


class CanonEntryProposal(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    category: str = "concept"
    content: Optional[str] = None


class StyleProposal(BaseModel):
    pov: Optional[str] = None
    tense: Optional[str] = None
    tone: Optional[str] = None
    comps: Optional[str] = None


class CommitPayload(BaseModel):
    """The author's REVIEWED selection — only these are written."""

    characters: list[CharacterProposal] = Field(default_factory=list)
    canon_entries: list[CanonEntryProposal] = Field(default_factory=list)
    style: Optional[StyleProposal] = None
    synopsis: Optional[str] = None


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


@router.post(
    "/books/{book_id}/sources/{source_id}/extract",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_extraction(
    book_id: UUID,
    source_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Kick off extraction of a source into proposed canon (background job)."""
    book = await _owned_book(book_id, current_user, db)
    source = (
        await db.execute(
            select(SourceORM).where(
                SourceORM.id == source_id,
                SourceORM.book_id == book_id,
                SourceORM.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found in this book",
        )
    if not source.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source has no stored content to extract from",
        )
    await check_user_budget(db, current_user.id)

    run = await enqueue_extraction(
        db, book_id=book.id, source_id=source.id, user_id=current_user.id
    )
    await db.commit()
    return {"run_id": str(run.id), "status": run.status}


async def _owned_run(
    book_id: UUID, run_id: UUID, current_user: UserORM, db: AsyncSession
) -> ExtractionRunORM:
    await _owned_book(book_id, current_user, db)
    run = (
        await db.execute(
            select(ExtractionRunORM).where(
                ExtractionRunORM.id == run_id, ExtractionRunORM.book_id == book_id
            )
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Extraction run not found"
        )
    return run


@router.get("/books/{book_id}/extractions/{run_id}", response_model=dict)
async def get_extraction(
    book_id: UUID,
    run_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The extraction run + its proposals, for the review screen."""
    run = await _owned_run(book_id, run_id, current_user, db)
    return {
        "id": str(run.id),
        "source_id": str(run.source_id) if run.source_id else None,
        "status": run.status,
        "proposals": run.proposals or {},
        "error": run.error,
    }


@router.post("/books/{book_id}/extractions/{run_id}/commit", response_model=dict)
async def commit_extraction(
    book_id: UUID,
    run_id: UUID,
    payload: CommitPayload,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Write the author's reviewed selection as real canon + 'imported' versions.

    This is the ONLY path that writes canon (docs/BRD.md R4.4). The review screen's
    contract is honored consistently: an approved item is NEVER silently dropped.
    Every approved character, canon entry, style, and synopsis whose name already
    exists is MERGED (reviewed fields applied + versioned), not skipped — the only
    non-applied items are blank-named ones, and those are reported in `skipped` so
    the UI can surface them rather than redirecting as if all succeeded (PR review
    findings #1 across three rounds).

    The response reports `created` / `updated` per type plus `skipped`, and each
    committed character is linked to this source (`source_characters`) so a merged
    or multi-source character stays reachable from the source (PR review #2).
    """
    book = await _owned_book(book_id, current_user, db)
    run = await _owned_run(book_id, run_id, current_user, db)
    result = {
        "characters": {"created": [], "updated": []},
        "canon_entries": {"created": [], "updated": []},
        "style": None,  # None | "created" | "updated"
        "synopsis": None,  # None | "created" | "updated"
        "skipped": [],  # [{type, name, reason}] — nothing is silently dropped
    }
    # Every character committed here (created OR merged) is linked to the source.
    committed_char_ids: list[str] = []

    existing_chars = {
        c.name: c
        for c in (
            await db.execute(
                select(CharacterORM).where(CharacterORM.book_id == book_id)
            )
        )
        .scalars()
        .all()
    }
    existing_entries = {
        e.name: e
        for e in (
            await db.execute(
                select(CanonEntryORM).where(CanonEntryORM.book_id == book_id)
            )
        )
        .scalars()
        .all()
    }

    for c in payload.characters:
        if not c.name or not c.name.strip():
            result["skipped"].append(
                {"type": "character", "name": c.name, "reason": "blank name"}
            )
            continue
        row = existing_chars.get(c.name)
        is_new = row is None
        if is_new:
            row = CharacterORM(
                book_id=book.id,
                user_id=current_user.id,
                source_id=run.source_id,
                name=c.name,
                role=c.role,
                description=c.description,
            )
            db.add(row)
            existing_chars[c.name] = row
        else:
            # MERGE approved fields into the existing (possibly name-only) row so
            # the reviewed role/description aren't lost (PR review #1).
            if c.role:
                row.role = c.role
            if c.description:
                row.description = c.description
        await db.flush()
        # FULL snapshot so an imported version is a complete-state restore.
        await versions_repo.snapshot(
            db,
            book_id=book.id,
            entity_type="character",
            entity_id=row.id,
            content=character_snapshot(row),
            reason="imported",
            created_by=current_user.id,
        )
        result["characters"]["created" if is_new else "updated"].append(str(row.id))
        committed_char_ids.append(str(row.id))

    for e in payload.canon_entries:
        if not e.name or not e.name.strip():
            result["skipped"].append(
                {"type": "canon_entry", "name": e.name, "reason": "blank name"}
            )
            continue
        if e.category not in CATEGORIES:
            e.category = "concept"
        row = existing_entries.get(e.name)
        is_new = row is None
        if is_new:
            row = CanonEntryORM(
                book_id=book.id, name=e.name, category=e.category, content=e.content
            )
            db.add(row)
            existing_entries[e.name] = row
        else:
            # MERGE: an approved edit to an EXISTING entry must apply, not be
            # silently skipped — the review screen checks it by default and the
            # author expects their change written (PR review #1, round 3).
            row.category = e.category
            row.content = e.content
        await db.flush()
        await versions_repo.snapshot(
            db,
            book_id=book.id,
            entity_type="canon_entry",
            entity_id=row.id,
            content={
                "name": row.name,
                "category": row.category,
                "content": row.content,
                "position": row.position,
            },
            reason="imported",
            created_by=current_user.id,
        )
        result["canon_entries"]["created" if is_new else "updated"].append(str(row.id))

    if payload.style is not None:
        style = (
            await db.execute(
                select(StyleGuideORM).where(StyleGuideORM.book_id == book_id)
            )
        ).scalar_one_or_none()
        style_existed = style is not None
        if style is None:
            style = StyleGuideORM(book_id=book.id)
            db.add(style)
        for field_name in ("pov", "tense", "tone", "comps"):
            value = getattr(payload.style, field_name)
            if value:
                setattr(style, field_name, value)
        await db.flush()
        await versions_repo.snapshot(
            db,
            book_id=book.id,
            entity_type="style_guide",
            entity_id=style.id,
            content={
                f: getattr(style, f)
                for f in ("pov", "tense", "tone", "comps", "sample_prose")
            },
            reason="imported",
            created_by=current_user.id,
        )
        result["style"] = "updated" if style_existed else "created"

    if payload.synopsis:
        # Always versioned, and applied whether or not a synopsis already exists —
        # an approved synopsis edit must not be silently dropped just because the
        # book already had one (PR review #1, round 3). record_synopsis snapshots
        # the prior value first, so nothing is lost.
        had_synopsis = bool(book.synopsis)
        await record_synopsis(
            db, book, payload.synopsis, reason="imported", created_by=current_user.id
        )
        result["synopsis"] = "updated" if had_synopsis else "created"

    # Link every committed character to this source (idempotent). This M2M — not
    # Character.source_id, which is single and goes NULL on file delete — is what
    # makes a merged/multi-source character reachable from the source (review #2).
    if run.source_id and committed_char_ids:
        already_linked = set(
            (
                await db.execute(
                    select(source_characters.c.character_id).where(
                        source_characters.c.source_id == run.source_id
                    )
                )
            )
            .scalars()
            .all()
        )
        new_links = [
            {"source_id": run.source_id, "character_id": UUID(cid)}
            for cid in committed_char_ids
            if UUID(cid) not in already_linked
        ]
        if new_links:
            await db.execute(source_characters.insert(), new_links)

    # Voice indexing is a RETRYABLE job over the EXPLICIT approved character IDs
    # (created + merged), so a merged existing character is indexed too, and a
    # transient vector failure is retried per-source, not skipped (PR review #2/#3).
    target_ids = result["characters"]["created"] + result["characters"]["updated"]
    if run.source_id and target_ids:
        await jobs_repo.enqueue(
            db,
            kind="index_characters_voice",
            payload={
                "source_id": str(run.source_id),
                "book_id": str(book.id),
                "user_id": str(current_user.id),
                "character_ids": target_ids,
            },
            user_id=current_user.id,
            max_attempts=3,
        )

    run.status = "committed"
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Commit conflicted with existing canon; review and retry",
        )
    return {"run_id": str(run.id), "status": run.status, "result": result}
