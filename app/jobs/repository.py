"""Persistence primitives for durable background jobs.

All functions operate on an explicit AsyncSession and never commit — the
caller owns the transaction. That is what makes enqueue atomic with the
domain row it belongs to (scene/manuscript/report + job commit together on
the request session), and what lets the worker wrap claim/finish in its own
short transactions.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import Job
from app.jobs import notify_enqueued

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
    _notify_on_commit(session)
    return job


def _notify_on_commit(session: AsyncSession) -> None:
    """Wake the worker once this session's transaction actually commits.

    Not at flush time. The caller owns the commit, so a wake fired here would
    race it: the worker looks, the row is not visible yet, and with nothing else
    queued it goes back to sleeping indefinitely — stranding a durable job until
    the next enqueue or a restart. No bounded number of re-checks fixes that,
    because the caller may hold the transaction open for arbitrarily long (slow
    commit, more work after the enqueue); it only narrows the window. Hooking
    `after_commit` closes it: when the wake fires the row is committed and
    claimable, so a single look always finds it.

    `once=True` removes the listener after it fires. Several enqueues in one
    transaction register several listeners, all of which fire and unregister on
    that commit — harmless, since a wake is idempotent. A rolled-back
    transaction leaves the listener armed for that session's next commit, which
    costs at most one spurious wake (the worker looks, finds nothing, sleeps).
    """
    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_commit", once=True)
    def _fire(_session) -> None:  # pragma: no cover - exercised via enqueue
        notify_enqueued()


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


async def pause(
    session: AsyncSession,
    job: Job,
    *,
    available_at: datetime,
    reason: str,
) -> None:
    """Re-queue a job for later WITHOUT consuming a retry (quota exhaustion).

    A pause is not a failure — the work is fine, the quota is temporarily gone
    (docs/BRD.md R7.2). Unlike mark_failed it does not count against max_attempts:
    claim_one incremented `attempts`, so decrement it back, set available_at to
    when quota returns, and let the worker resume the job automatically. No lost
    work, no double-spend.
    """
    job.status = "queued"
    job.available_at = available_at
    job.attempts = max(0, job.attempts - 1)
    job.error = reason
    job.locked_at = None
    job.locked_by = None
    await session.flush()


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


async def idle_state(
    session: AsyncSession,
) -> tuple[datetime | None, bool]:
    """When the worker must next look, and whether anything is running.

    One round trip answering both questions the idle worker has: the earliest
    `available_at` among queued jobs (a retry backoff or a quota pause that is
    not due yet) and whether any job is still marked 'running' (so the stale
    reaper has something to reap). Both None/False means there is genuinely
    nothing to do and the worker can sleep until enqueue() wakes it, issuing no
    further query — which is what lets Neon's compute autosuspend.
    """
    # type_ is load-bearing: a bare func.min() loses the column's
    # DateTime(timezone=True) and SQLAlchemy hands back a NAIVE datetime, on
    # Postgres as well as sqlite. Naive would mean guessing a zone to compute
    # "seconds until due" — and a wrong guess is a worker that sleeps hours past
    # a quota resume, or spins. Carry the type so the value comes back aware.
    next_available = (
        select(func.min(Job.available_at, type_=Job.available_at.type))
        .where(Job.status == "queued")
        .scalar_subquery()
    )
    running = (
        select(func.count())
        .select_from(Job)
        .where(Job.status == "running")
        .scalar_subquery()
    )
    row = (await session.execute(select(next_available, running))).one()
    return row[0], bool(row[1])
