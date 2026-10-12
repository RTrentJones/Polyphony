"""The running job's context, handed from the worker to handlers and hooks.

Handlers keep their `run(payload)` signature; the worker sets this context
around every run and hook call so a handler can read its job's checkpoint and
save a new one (SPEC-08), and a hook can read why the job paused, died or was
cancelled (SPEC-07). Outside the worker (tests calling a handler directly, the
CLI) `current_job()` returns an empty context whose save and note are no-ops.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.jobs.cancel import JobCancelled


@dataclass
class JobContext:
    job_id: Optional[UUID] = None
    kind: str = ""
    checkpoint: Optional[dict] = None
    # Set for hooks: the job's error (on_dead) and when it resumes (on_pause).
    error: Optional[str] = None
    resume_at: Optional[datetime] = None

    async def save(self, data: dict) -> None:
        """Checkpoint the last completed model call; stop if cancel was asked.

        The checkpoint seam is also the cancellation seam: one short
        transaction writes the checkpoint and reads `cancel_requested`.
        """
        self.checkpoint = data
        if self.job_id is None:
            return
        from app.jobs import repository as jobs_repo

        if await jobs_repo.save_checkpoint(self.job_id, data):
            raise JobCancelled("cancelled by the author")

    async def note(self, message: str) -> None:
        """Record an informational note on the job (e.g. a canon-drift restart)."""
        if self.job_id is None:
            return
        from app.jobs import repository as jobs_repo

        await jobs_repo.set_note(self.job_id, message)


_current: ContextVar[Optional[JobContext]] = ContextVar("current_job", default=None)


def current_job() -> JobContext:
    return _current.get() or JobContext()


@contextmanager
def job_context(ctx: JobContext):
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
