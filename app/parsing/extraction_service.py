"""Enqueue a Source -> proposed Canon extraction run (shared by upload + /extract).

Centralized so upload, reprocess, and the explicit /extract endpoint all create
canon the SAME way: as reviewable proposals, never a direct write (docs/BRD.md
R4.4). The reviewed commit is the only path that writes canon.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import ExtractionRun, Job, Source
from app.jobs import repository as jobs_repo


async def enqueue_extraction(
    db: AsyncSession, *, book_id: UUID, source_id: UUID, user_id: UUID
) -> tuple[ExtractionRun, Job]:
    """Create a pending ExtractionRun and enqueue the extract_canon job. No commit.

    One in-flight extraction per source CONTENT (SPEC-07): while one is queued,
    paused or running, a repeated reprocess returns that run and job instead of
    starting a second. Keyed on the content hash, because an edit that changes
    the text must extract the new text even if the old run is mid-call.
    """
    source = await db.get(Source, source_id)
    content_hash = getattr(source, "content_hash", None) or ""
    dedupe_key = f"extract:{source_id}:{content_hash[:16]}"
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None and existing.domain_id is not None:
        run = await db.get(ExtractionRun, existing.domain_id)
        if run is not None:
            return run, existing
    run = ExtractionRun(
        book_id=book_id,
        source_id=source_id,
        user_id=user_id,
        status="pending",
    )
    db.add(run)
    await db.flush()
    job = await jobs_repo.enqueue(
        db,
        kind="extract_canon",
        payload={
            "run_id": str(run.id),
            "source_id": str(source_id),
            "user_id": str(user_id),
        },
        user_id=user_id,
        max_attempts=2,
        dedupe_key=dedupe_key,
        domain=("extraction_run", run.id),
    )
    if job.domain_id != run.id:  # an identical request won the race
        await db.delete(run)
        await db.flush()
        run = await db.get(ExtractionRun, job.domain_id)
    return run, job
