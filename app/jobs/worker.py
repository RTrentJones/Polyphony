"""Single-consumer worker loop for durable jobs.

Started from the app lifespan. One job runs at a time, which (deliberately)
serializes all LLM-heavy background work the way the old process-wide
Semaphore(1) in app/orchestration/runner.py did — but against a durable
queue, so queued work survives restarts and stale running jobs are reaped.
The fine-grained per-call LLM pacer (app/llm/pacing.py) is unchanged.
"""

import asyncio
import socket
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Job
from app.jobs import repository as jobs_repo
from app.jobs.handlers import HANDLERS

logger = setup_logging("jobs.worker")

REAP_INTERVAL_SECONDS = 60
ERROR_BACKOFF_SECONDS = 5


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
        self._last_reap: datetime | None = None

    async def start(self) -> None:
        # Boot recovery: in a single-container deployment any 'running' job at
        # startup was orphaned by the previous process — requeue or kill it now.
        try:
            await self._reap(stale_after=timedelta(0))
        except Exception as e:
            log_error(logger, e, context={"event": "job_boot_reap_failed"})
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        log_business_event(logger, "job_worker_started", worker_id=self.worker_id)

    async def stop(self) -> None:
        self._stop.set()
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
                ran = False
                await self._wait(ERROR_BACKOFF_SECONDS)
                continue
            if not ran:
                await self._wait(self.poll_interval)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

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
        if handler is None:
            error = f"unknown job kind: {kind}"
        else:
            try:
                await handler.run(payload)
            except Exception as e:
                error = str(e) or type(e).__name__

        async with get_async_session() as session:
            # Re-attach the job in this session.
            db_job = await session.get(Job, job_id)
            if db_job is None:  # deleted underneath us (user cascade)
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
