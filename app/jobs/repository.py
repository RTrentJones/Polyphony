"""Persistence primitives for durable background jobs.

All functions operate on an explicit AsyncSession and never commit — the
caller owns the transaction. That is what makes enqueue atomic with the
domain row it belongs to (scene/manuscript/report + job commit together on
the request session), and what lets the worker wrap claim/finish in its own
short transactions.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_async_session
from app.core.orm_models import Job
from app.jobs import notify_enqueued

# Retry n (1-based) becomes available after BASE * 2**(n-1), capped.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 15 * 60

# Statuses that hold a dedupe key (a paused job is 'queued' with paused_since).
ACTIVE_STATUSES = ("queued", "running")
# The error text a quota pause leaves on the job while it waits.
PAUSE_REASON = "quota exhausted"


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
    dedupe_key: str | None = None,
    domain: tuple[str, UUID] | None = None,
) -> Job:
    """Add a queued job to the session (flushed, NOT committed).

    With a `dedupe_key`, an in-flight job holding that key is returned instead
    of a new one (SPEC-07): the caller compares `job.domain_id` with its own row
    to tell. The partial unique index is the backstop for two requests racing
    past the lookup; on Postgres the loser's insert is rolled back to a
    savepoint and it gets the winner's job.
    """
    if dedupe_key is not None:
        existing = await find_active(session, dedupe_key)
        if existing is not None:
            return existing
    job = Job(
        kind=kind,
        payload=payload,
        user_id=user_id,
        status="queued",
        max_attempts=max_attempts,
        available_at=available_at or _utcnow(),
        dedupe_key=dedupe_key,
        domain_type=domain[0] if domain else None,
        domain_id=domain[1] if domain else None,
    )
    if dedupe_key is not None and session.bind.dialect.name == "postgresql":
        try:
            async with session.begin_nested():
                session.add(job)
                await session.flush()
        except IntegrityError:
            existing = await find_active(session, dedupe_key)
            if existing is None:
                raise
            return existing
    else:
        session.add(job)
        await session.flush()
    _notify_on_commit(session)
    return job


async def find_active(session: AsyncSession, dedupe_key: str) -> Job | None:
    """The in-flight (queued, paused or running) job holding `dedupe_key`."""
    return (
        await session.execute(
            select(Job)
            .where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_STATUSES))
            .limit(1)
        )
    ).scalar_one_or_none()


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
    job.paused_since = None
    job.checkpoint = None  # job-scoped resume state; done with it
    if job.error == PAUSE_REASON:
        job.error = None  # the pause is over; an informational note stays
    await session.flush()


async def mark_cancelled(session: AsyncSession, job: Job) -> None:
    """Terminal 'cancelled' (SPEC-07). The checkpoint goes with it (SPEC-08), so
    a cancelled job leaves no half-written state anywhere."""
    job.status = "cancelled"
    job.finished_at = _utcnow()
    job.locked_at = None
    job.locked_by = None
    job.paused_since = None
    job.checkpoint = None
    await session.flush()


async def mark_failed(session: AsyncSession, job: Job, error: str) -> bool:
    """Record a failed attempt. Returns True if the job went dead.

    Requeues with exponential backoff while attempts remain; otherwise the
    job is dead and the caller should run the kind's on_dead hook so the
    related domain row doesn't stay 'processing' forever.
    """
    now = _utcnow()
    job.error = error
    job.paused_since = None  # a real failure ends any pause streak
    if job.attempts >= job.max_attempts:
        job.status = "dead"
        job.finished_at = now
        job.locked_at = None
        job.locked_by = None
        job.checkpoint = None
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
    if job.paused_since is None:
        job.paused_since = _utcnow()
    job.pause_count = (job.pause_count or 0) + 1
    await session.flush()


def pause_expired(job: Job, *, max_hours: float, now: datetime | None = None) -> bool:
    """Has this job been paused (without progress) longer than the bound?"""
    if job.paused_since is None or max_hours <= 0:
        return False
    since = job.paused_since
    if since.tzinfo is None:  # sqlite hands back naive datetimes
        since = since.replace(tzinfo=timezone.utc)
    return (now or _utcnow()) - since >= timedelta(hours=max_hours)


async def save_checkpoint(job_id: UUID, data: dict) -> bool:
    """Persist a job's checkpoint in its own short transaction (SPEC-08).

    Unlike the rest of this module it commits: a checkpoint that waited for the
    job's outcome would be lost by exactly the pause it exists for. Saving a
    checkpoint is progress, so it also ends the pause streak that bounds a pause.
    Returns whether the author asked to cancel, so the caller can stop at the
    same seam (SPEC-07).
    """
    async with get_async_session() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return False
        # Round-trip through JSON so a caller's later in-place mutation of `data`
        # cannot alias the stored value, and flag it: the column is not a
        # mutation-tracking type.
        job.checkpoint = json.loads(json.dumps(data, default=str))
        flag_modified(job, "checkpoint")
        job.paused_since = None
        cancel = bool(job.cancel_requested)
        await session.commit()
        return cancel


async def set_note(job_id: UUID, note: str) -> None:
    """Record an informational note on the job's error field (own transaction)."""
    async with get_async_session() as session:
        job = await session.get(Job, job_id)
        if job is not None:
            job.error = note
            await session.commit()


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
