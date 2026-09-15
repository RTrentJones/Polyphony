"""Single-consumer worker loop for durable jobs.

Started from the app lifespan. One job runs at a time, which (deliberately)
serializes all LLM-heavy background work the way the old process-wide
scene-runner Semaphore(1) did — but against a durable queue, so queued work
survives restarts and stale running jobs are reaped.
The fine-grained per-call LLM pacer (app/llm/pacing.py) is unchanged.

WOKEN, NOT POLLED. An idle worker issues no query at all: it sleeps on an
asyncio.Event that `repository.enqueue` sets (app/jobs/__init__.py), and when
something IS pending but not yet due — a retry backoff, a quota pause — it
sleeps until exactly that moment instead of ticking. This is a hosting
constraint, not a micro-optimisation: Postgres is Neon, whose compute
autosuspends after ~5 minutes of connection inactivity and is billed for every
hour it stays awake. The previous 2-second empty-queue poll kept the compute
resident 24/7 (~180 CU-hours/month, near the whole free allowance) purely to
learn, 43,200 times a day, that there was nothing to do.

The same constraint rules out a "cheap" fallback poll. Because autosuspend
waits ~5 minutes after the LAST query, an isolated wake-up costs ~5 minutes of
compute no matter how small the query — so even hourly polling would cost
tens of hours a month. Hence the default of sleeping indefinitely, with
JOB_IDLE_POLL_SECONDS available as an opt-in escape hatch (see below).

Single process, single worker (ADR-001): the in-process wake is sufficient
because every enqueue happens in this process. A second writer (a second
container, an out-of-band SQL insert) would not wake this loop; that deployment
needs Postgres LISTEN/NOTIFY, or JOB_IDLE_POLL_SECONDS set to accept the cost.
"""

import asyncio
import socket
import uuid
from datetime import datetime, timedelta, timezone

from app import jobs
from app.core.config import settings
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Job
from app.jobs import repository as jobs_repo
from app.jobs.handlers import HANDLERS
from app.llm.errors import QuotaExhaustedError
from app.llm.tier import get_tier

logger = setup_logging("jobs.worker")

REAP_INTERVAL_SECONDS = 60
ERROR_BACKOFF_SECONDS = 5
# Looks taken after a wake before trusting "nothing queued". enqueue() notifies
# on flush but the CALLER owns the commit, so the first look can legitimately
# race ahead of the row becoming visible. A handful of cheap re-checks closes
# that window; they cost queries only in the moments right after an enqueue.
SETTLE_POLLS = 3


class JobWorker:
    def __init__(
        self,
        *,
        poll_interval: float,
        stale_after: timedelta,
        worker_id: str | None = None,
    ):
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._settle = 0
        self._last_reap: datetime | None = None

    def notify(self) -> None:
        """Wake the loop — registered with app.jobs so enqueue() can call it."""
        self._settle = SETTLE_POLLS
        self._wake.set()

    async def start(self) -> None:
        # Boot recovery: in a single-container deployment any 'running' job at
        # startup was orphaned by the previous process — requeue or kill it now.
        try:
            await self._reap(stale_after=timedelta(0))
        except Exception as e:
            log_error(logger, e, context={"event": "job_boot_reap_failed"})
        # The boot reap counts as this cycle's reap; without this the first
        # run_once would immediately reap again.
        self._last_reap = datetime.now(timezone.utc)
        self._stop.clear()
        self._wake.clear()
        # Drain whatever survived the restart before settling into the idle wait.
        self._settle = SETTLE_POLLS
        jobs.set_notifier(self.notify)
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        log_business_event(logger, "job_worker_started", worker_id=self.worker_id)

    async def stop(self) -> None:
        jobs.set_notifier(None)
        self._stop.set()
        self._wake.set()  # break an indefinite idle wait immediately
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        log_business_event(logger, "job_worker_stopped", worker_id=self.worker_id)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = await self.run_once()
            except Exception as e:
                # DB down, etc. — log and back off; the loop must never die.
                log_error(logger, e, context={"event": "job_worker_loop_error"})
                await self._wait(ERROR_BACKOFF_SECONDS)
                continue
            if ran:
                continue  # drain: there may be more behind it
            if self._settle > 0:
                # Freshly woken — re-check a bounded number of times so an
                # enqueue whose commit landed just after our look isn't missed.
                self._settle -= 1
                await self._wait(self.poll_interval)
                continue
            await self._idle()

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _idle(self) -> None:
        """Sleep until there is a reason to touch the database again.

        Clearing the wake flag BEFORE asking the database is what makes this
        race-free: an enqueue arriving during the query re-sets the flag and the
        wait below returns at once, rather than the wake being cleared away.

        The clear must also stay the FIRST statement here, with no await between
        the caller's "nothing to settle" check and this line. Nothing else can
        run in that gap today, so a notify() cannot be cleared away unseen; an
        await inserted above would open exactly that gap, and the cost of losing
        a wake is a queued job that sits until the next enqueue or restart.
        """
        self._wake.clear()
        try:
            timeout = await self._next_deadline()
        except Exception as e:
            log_error(logger, e, context={"event": "job_idle_deadline_failed"})
            timeout = ERROR_BACKOFF_SECONDS
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _next_deadline(self) -> float | None:
        """Seconds to sleep, or None to sleep until woken.

        None is the common case and the whole point — nothing queued, nothing
        running, so no query until enqueue() wakes us and Neon can autosuspend.
        A number means something is pending but not due: a paused or backed-off
        job (sleep until it IS due) or a running job (so the stale reaper has
        something to reap).
        """
        async with get_async_session() as session:
            next_available, has_running = await jobs_repo.idle_state(session)

        deadlines: list[float] = []
        if next_available is not None:
            # sqlite hands back naive datetimes for timezone=True columns.
            if next_available.tzinfo is None:
                next_available = next_available.replace(tzinfo=timezone.utc)
            due_in = (next_available - datetime.now(timezone.utc)).total_seconds()
            deadlines.append(max(0.0, due_in))
        if has_running:
            deadlines.append(float(REAP_INTERVAL_SECONDS))
        # Opt-in ceiling for deployments where this process is not the only
        # writer. Zero (the default) means no ceiling — see the module docstring
        # on why a fallback poll is not free.
        if settings.JOB_IDLE_POLL_SECONDS > 0:
            deadlines.append(float(settings.JOB_IDLE_POLL_SECONDS))
        return min(deadlines) if deadlines else None

    async def run_once(self) -> bool:
        """Reap (throttled) + claim + execute at most one job.

        Returns True if a job was executed (poll again immediately).
        """
        now = datetime.now(timezone.utc)
        if (
            self._last_reap is None
            or (now - self._last_reap).total_seconds() >= REAP_INTERVAL_SECONDS
        ):
            self._last_reap = now
            await self._reap(stale_after=self.stale_after)

        async with get_async_session() as session:
            job = await jobs_repo.claim_one(session, worker_id=self.worker_id)
            if job is None:
                return False
            await session.commit()
            job_id, kind, payload, attempts = (
                job.id,
                job.kind,
                job.payload,
                job.attempts,
            )

        log_business_event(
            logger, "job_claimed", job_id=str(job_id), kind=kind, attempts=attempts
        )

        handler = HANDLERS.get(kind)
        error: str | None = None
        pause_after: float | None = None  # set => quota pause, not a failure
        if handler is None:
            error = f"unknown job kind: {kind}"
        else:
            try:
                await handler.run(payload)
            except QuotaExhaustedError as e:
                # Free tier: pause and resume; paid: a 429 is a real error.
                if get_tier().on_quota == "pause":
                    pause_after = e.reset_after or settings.QUOTA_PAUSE_SECONDS
                else:
                    error = str(e) or "quota exhausted"
            except Exception as e:
                error = str(e) or type(e).__name__

        async with get_async_session() as session:
            # Re-attach the job in this session.
            db_job = await session.get(Job, job_id)
            if db_job is None:  # deleted underneath us (user cascade)
                return True
            if pause_after is not None:
                resume_at = datetime.now(timezone.utc) + timedelta(seconds=pause_after)
                await jobs_repo.pause(
                    session, db_job, available_at=resume_at, reason="quota exhausted"
                )
                await session.commit()
                log_business_event(
                    logger,
                    "job_paused",
                    job_id=str(job_id),
                    kind=kind,
                    resume_at=resume_at.isoformat(),
                )
                # Reflect the pause on the domain row (e.g. scene -> 'paused') so
                # the UI shows "waiting for quota", not a stuck "generating".
                if handler is not None and handler.on_pause is not None:
                    try:
                        await handler.on_pause(payload)
                    except Exception as e:
                        log_error(logger, e, context={"event": "job_on_pause_failed"})
                return True
            if error is None:
                await jobs_repo.mark_succeeded(session, db_job)
                await session.commit()
                log_business_event(
                    logger, "job_succeeded", job_id=str(job_id), kind=kind
                )
                return True
            if handler is None:
                # Unknown kind can never succeed: kill it regardless of attempts.
                db_job.attempts = db_job.max_attempts
            went_dead = await jobs_repo.mark_failed(session, db_job, error)
            await session.commit()

        if went_dead:
            log_business_event(
                logger,
                "job_dead",
                job_id=str(job_id),
                kind=kind,
                attempts=attempts,
                error=error,
            )
            if handler is not None and handler.on_dead is not None:
                try:
                    await handler.on_dead(payload)
                except Exception as e:
                    log_error(logger, e, context={"event": "job_on_dead_failed"})
        else:
            log_business_event(
                logger,
                "job_retry",
                job_id=str(job_id),
                kind=kind,
                attempts=attempts,
                error=error,
            )
        return True

    async def _reap(self, *, stale_after: timedelta) -> None:
        async with get_async_session() as session:
            results = await jobs_repo.reap_stale(session, stale_after=stale_after)
            await session.commit()
            reaped = [(j.id, j.kind, j.payload, dead) for j, dead in results]
        for job_id, kind, payload, went_dead in reaped:
            log_business_event(
                logger,
                "job_dead" if went_dead else "job_retry",
                job_id=str(job_id),
                kind=kind,
                reaped=True,
            )
            if went_dead:
                handler = HANDLERS.get(kind)
                if handler is not None and handler.on_dead is not None:
                    try:
                        await handler.on_dead(payload)
                    except Exception as e:
                        log_error(logger, e, context={"event": "job_on_dead_failed"})
