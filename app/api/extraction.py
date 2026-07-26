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


# Proposals allow a blank name so the SERVER (never the frontend) decides what to
# do with it — it skips + surfaces the item rather than the client silently
# filtering it out (PR review #1, round 6). `model_fields_set` on these models is
# load-bearing: it distinguishes "field omitted" (leave untouched) from "field
# supplied as '' / null" (apply it, so an author can CLEAR an existing value).
class CharacterProposal(BaseModel):
    name: str = Field(..., max_length=255)
    role: Optional[str] = None
    description: Optional[str] = None


class CanonEntryProposal(BaseModel):
    name: str = Field(..., max_length=255)
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

    Commit is exactly-once: the run row is locked FOR UPDATE and only a `ready`
    run is accepted. A double-submit or a retry after a lost response therefore
    409s instead of appending duplicate versions and re-spending embedding budget
    (PR review #2, round 6). `with_for_update` is a no-op on sqlite (single writer)
    and a real row lock on Postgres, which serializes concurrent commits.
    """
    book = await _owned_book(book_id, current_user, db)
    # Lock the run for the duration of the transaction so a concurrent commit
    # blocks here, then sees status != 'ready' and is rejected.
    run = (
        await db.execute(
            select(ExtractionRunORM)
            .where(
                ExtractionRunORM.id == run_id,
                ExtractionRunORM.book_id == book_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Extraction run not found"
        )
    if run.status != "ready":
        # Already committed / failed / still pending — never write or enqueue twice.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Extraction run is '{run.status}', not 'ready' — nothing committed",
        )
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
        supplied = c.model_fields_set
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
            # MERGE the SUPPLIED fields into the existing (possibly name-only) row.
            # Keyed on model_fields_set, not truthiness, so an author can CLEAR a
            # role/description (send "") and an omitted field is left untouched —
            # matching the character mutation API (PR review #1, round 6).
            if "role" in supplied:
                row.role = c.role
            if "description" in supplied:
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
        supplied = e.model_fields_set
        if "category" in supplied and e.category not in CATEGORIES:
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
            # MERGE the supplied fields (an approved edit to an EXISTING entry must
            # apply, and "" must clear); an omitted field is left untouched
            # (PR review #1, rounds 3 + 6).
            if "category" in supplied:
                row.category = e.category
            if "content" in supplied:
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
        # Apply exactly the supplied style fields (incl. "" to clear a pov/tone/…),
        # leaving omitted fields untouched (PR review #1, round 6). The review UI
        # leaves style unapproved when the proposal is all-empty, so an untouched
        # empty proposal never reaches here to clobber an existing guide.
        supplied = payload.style.model_fields_set
        for field_name in ("pov", "tense", "tone", "comps"):
            if field_name in supplied:
                setattr(style, field_name, getattr(payload.style, field_name))
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

    if "synopsis" in payload.model_fields_set:
        # Applied whenever SUPPLIED (present in the request), whether or not a
        # synopsis already exists, and a blank value CLEARS it — an approved
        # synopsis edit (or deletion) must not be silently dropped (PR review #1,
        # rounds 3 + 6). record_synopsis snapshots the prior value first and
        # versions the change, so nothing is lost.
        had_synopsis = bool(book.synopsis)
        new_value = payload.synopsis or None  # "" / null / whitespace -> cleared
        if new_value is not None and not new_value.strip():
            new_value = None
        await record_synopsis(
            db, book, new_value, reason="imported", created_by=current_user.id
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
