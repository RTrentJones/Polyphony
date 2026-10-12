"""Enqueue a Source -> proposed Canon extraction run (shared by upload + /extract).

Centralized so upload, reprocess, and the explicit /extract endpoint all create
canon the SAME way: as reviewable proposals, never a direct write (docs/BRD.md
R4.4). The reviewed commit is the only path that writes canon.
"""

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import ExtractionRun, Source
from app.jobs import repository as jobs_repo
from app.parsing.extraction_plan import preflight_extraction


class ExtractionTooLarge(Exception):
    """The preflight refused: the run cannot finish within the budget window."""

    def __init__(self, preflight: dict):
        super().__init__(preflight.get("message", "extraction too large"))
        self.preflight = preflight


def too_large_http(e: ExtractionTooLarge) -> HTTPException:
    """The refusal as an API error, carrying the estimate and the alternative."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"message": str(e), "preflight": e.preflight},
    )


async def enqueue_extraction(
    db: AsyncSession,
    *,
    book_id: UUID,
    source_id: UUID,
    user_id: UUID,
    text: Optional[str] = None,
    max_chapters: Optional[int] = None,
) -> ExtractionRun:
    """Preflight, then create a pending ExtractionRun and enqueue the
    extract_canon job. No commit.

    With `text`, the cost is estimated first (SPEC-09) and ExtractionTooLarge is
    raised — nothing created — when it cannot finish; the estimate rides on the
    returned run as `run.preflight`.

    One in-flight extraction per source CONTENT (SPEC-07): while one is queued,
    paused or running, a repeated request returns that run (and its job, as the
    transient `run.job_id`) instead of starting a second. Keyed on the content
    hash, because an edit that changes the text must extract the new text even
    if the old run is mid-call.
    """
    source = await db.get(Source, source_id)
    content_hash = (getattr(source, "content_hash", None) or "")[:16]
    dedupe_key = f"extract:{source_id}:{content_hash}:{max_chapters or 'all'}"
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None and existing.domain_id is not None:
        run = await db.get(ExtractionRun, existing.domain_id)
        if run is not None:
            run.preflight = None
            run.job_id = existing.id
            return run
    preflight = None
    if text is not None:
        preflight = await preflight_extraction(
            db, user_id, text, max_chapters=max_chapters
        )
        if not preflight["fits"]:
            raise ExtractionTooLarge(preflight)
    run = ExtractionRun(
        book_id=book_id,
        source_id=source_id,
        user_id=user_id,
        status="pending",
        progress=(
            {"phase": "queued", "windows_total": preflight["windows"]}
            if preflight
            else None
        ),
    )
    db.add(run)
    await db.flush()
    payload = {
        "run_id": str(run.id),
        "source_id": str(source_id),
        "user_id": str(user_id),
        # The revision this run extracts: an edit queues a new run, and this
        # one retires instead of extracting text it was not queued for.
        "content_hash": getattr(source, "content_hash", None),
    }
    if max_chapters:
        payload["max_chapters"] = int(max_chapters)
    job = await jobs_repo.enqueue(
        db,
        kind="extract_canon",
        payload=payload,
        user_id=user_id,
        max_attempts=2,
        dedupe_key=dedupe_key,
        domain=("extraction_run", run.id),
    )
    if job.domain_id != run.id:  # an identical request won the race
        await db.delete(run)
        await db.flush()
        run = await db.get(ExtractionRun, job.domain_id)
    run.preflight = preflight  # transient, for the enqueueing endpoint's response
    run.job_id = job.id  # transient, likewise (SPEC-07: every enqueue returns it)
    return run
