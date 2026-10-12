"""Fixes from the October integration review (docs/REVIEW-2026-10.md follow-up)."""

from datetime import timedelta

import pytest

from app.core.config import settings
from app.jobs import repository as jobs_repo
from app.jobs.handlers import Handler
from app.jobs.worker import JobWorker
from app.llm.errors import QuotaExhaustedError

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


def _bind(monkeypatch, session):
    import app.jobs.handlers as h
    import app.jobs.worker as w

    monkeypatch.setattr(w, "get_async_session", lambda: _Ctx(session))
    monkeypatch.setattr(h, "get_async_session", lambda: _Ctx(session))


async def test_cancel_requested_job_that_hits_quota_is_cancelled_not_paused(
    async_session, test_user, monkeypatch
):
    _bind(monkeypatch, async_session)
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    cancelled = []

    async def run(payload):
        job = (
            (
                await async_session.execute(
                    __import__("sqlalchemy").select(
                        __import__("app.core.orm_models", fromlist=["Job"]).Job
                    )
                )
            )
            .scalars()
            .first()
        )
        job.cancel_requested = True  # the author clicked Cancel mid-run
        await async_session.flush()
        raise QuotaExhaustedError("429", reset_after=30)

    async def on_cancel(payload):
        cancelled.append(True)

    import app.jobs.worker as w

    monkeypatch.setattr(w, "HANDLERS", {"k": Handler(run=run, on_cancel=on_cancel)})
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
    )
    await async_session.commit()
    await JobWorker(poll_interval=0.01, stale_after=timedelta(minutes=30)).run_once()
    await async_session.refresh(job)
    assert job.status == "cancelled" and job.paused_since is None
    assert cancelled == [True]


async def test_backoff_never_exceeds_the_remaining_wait_budget(monkeypatch):
    import asyncio

    from app.llm import pacing

    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    d = await pacing.backoff_after_429(120.0, 0, cap=5.0)
    assert d == 5.0 and slept == [5.0]


async def test_continuity_stops_between_chunks_when_cancelled(monkeypatch):
    import types

    from app.jobs.cancel import JobCancelled
    from app.planning import continuity as cont

    calls = []

    class Fake:
        async def generate(self, messages, **kw):
            calls.append(1)
            return types.SimpleNamespace(
                text='{"findings": []}', tokens_in=1, tokens_out=1
            )

    monkeypatch.setattr(cont, "get_llm_client", lambda: Fake())

    async def seam():
        raise JobCancelled("cancelled by the author")

    words = " ".join(f"w{i}" for i in range(cont.CHUNK_WORDS * 3))
    with pytest.raises(JobCancelled):
        await cont.run_continuity_check(words, "<canon>f</canon>", between_chunks=seam)
    assert len(calls) == 1  # stopped before spending the rest of the book
