"""Persistence primitives for durable background jobs.

All functions operate on an explicit AsyncSession and never commit — the
caller owns the transaction. That is what makes enqueue atomic with the
domain row it belongs to (scene/manuscript/report + job commit together on
the request session), and what lets the worker wrap claim/finish in its own
short transactions.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import Job

# Retry n (1-based) becomes available after BASE * 2**(n-1), capped.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 15 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict,
    user_id: UUID,
    max_attempts: int = 1,
    available_at: datetime | None = None,
) -> Job:
    """Add a queued job to the session (flushed, NOT committed)."""
    job = Job(
        kind=kind,
        payload=payload,
        user_id=user_id,
        status="queued",
        max_attempts=max_attempts,
        available_at=available_at or _utcnow(),
    )
    session.add(job)
    await session.flush()
    return job


async def claim_one(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> Job | None:
    """Claim the oldest available queued job, or None.

    Uses FOR UPDATE SKIP LOCKED on Postgres so concurrent claimers never
    block or double-claim. The sqlite dialect doesn't render FOR UPDATE,
    which is fine for the single-worker unit-test setup; the skip-locked
    semantics are covered by the RUN_PG_TESTS integration test.
    """
    now = now or _utcnow()
    stmt = (
        select(Job)
        .where(Job.status == "queued", Job.available_at <= now)
        .order_by(Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = now
    job.locked_by = worker_id
    job.started_at = now
    await session.flush()
    return job


async def mark_succeeded(session: AsyncSession, job: Job) -> None:
    job.status = "succeeded"
    job.finished_at = _utcnow()
    job.locked_at = None
    job.locked_by = None
    await session.flush()


async def mark_failed(session: AsyncSession, job: Job, error: str) -> bool:
    """Record a failed attempt. Returns True if the job went dead.

    Requeues with exponential backoff while attempts remain; otherwise the
    job is dead and the caller should run the kind's on_dead hook so the
    related domain row doesn't stay 'processing' forever.
    """
    now = _utcnow()
    job.error = error
    if job.attempts >= job.max_attempts:
        job.status = "dead"
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        await session.flush()
        return True
    delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1))
    job.status = "queued"
    job.available_at = now + timedelta(seconds=delay)
    job.locked_at = None
    job.locked_by = None
    await session.flush()
    return False


async def reap_stale(
    session: AsyncSession,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
) -> list[tuple[Job, bool]]:
    """Fail 'running' jobs whose lock is older than stale_after.

    A stale running job means its worker died mid-execution. Each goes back
    through mark_failed (requeue or dead). Returns (job, went_dead) pairs so
    the worker can run on_dead hooks for the dead ones.
    """
    now = now or _utcnow()
    cutoff = now - stale_after
    stmt = (
        select(Job)
        .where(Job.status == "running", Job.locked_at <= cutoff)
        .with_for_update(skip_locked=True)
    )
    stale = (await session.execute(stmt)).scalars().all()
    results: list[tuple[Job, bool]] = []
    for job in stale:
        went_dead = await mark_failed(
            session, job, "stale job: worker presumed dead before completion"
        )
        results.append((job, went_dead))
    return results
