"""Enqueue a Source -> proposed Canon extraction run (shared by upload + /extract).

Centralized so upload, reprocess, and the explicit /extract endpoint all create
canon the SAME way: as reviewable proposals, never a direct write (docs/BRD.md
R4.4). The reviewed commit is the only path that writes canon.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import ExtractionRun
from app.jobs import repository as jobs_repo


async def enqueue_extraction(
    db: AsyncSession, *, book_id: UUID, source_id: UUID, user_id: UUID
) -> ExtractionRun:
    """Create a pending ExtractionRun and enqueue the extract_canon job. No commit."""
    run = ExtractionRun(
        book_id=book_id,
        source_id=source_id,
        user_id=user_id,
        status="pending",
    )
    db.add(run)
    await db.flush()
    await jobs_repo.enqueue(
        db,
        kind="extract_canon",
        payload={
            "run_id": str(run.id),
            "source_id": str(source_id),
            "user_id": str(user_id),
        },
        user_id=user_id,
        max_attempts=2,
    )
    return run
