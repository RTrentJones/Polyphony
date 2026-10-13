"""Cooperative job cancellation (SPEC-07).

A running job cannot be killed mid-LLM-call safely, so cancellation is
cooperative: `POST /jobs/{id}/cancel` on a RUNNING job sets
`cancel_requested`, and workflows call `raise_if_cancelled` at their natural
seams — between model calls, the same seams checkpoints are written at
(SPEC-08). Queued and paused jobs are cancelled immediately by the endpoint.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID


class JobCancelled(RuntimeError):
    """The author cancelled this job; the worker records it as cancelled."""


async def raise_if_cancelled(job_id: Optional[UUID]) -> None:
    if job_id is None:
        return
    from app.core.database import get_async_session
    from app.core.orm_models import Job

    async with get_async_session() as session:
        job = await session.get(Job, job_id)
        if job is not None and getattr(job, "cancel_requested", False):
            raise JobCancelled("cancelled by the author")
