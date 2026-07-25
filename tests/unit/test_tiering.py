"""Tiering + free-first pause/resume (Phase 4, docs/BRD.md R7).

The load-bearing behaviour: on the free tier a quota-exhausted job PAUSES and
resumes with no lost work and no double-spend; on paid it fails fast. Graduation
is one config flip.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core import budget
from app.core.config import settings
from app.jobs import repository as jobs_repo
from app.jobs.handlers import Handler
from app.jobs.worker import JobWorker
from app.llm.errors import QuotaExhaustedError
from app.llm.tier import get_tier

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _bind_sessions(monkeypatch, async_session):
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))


def _worker():
    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="test-w"
    )


def _aware(dt: datetime) -> datetime:
    """sqlite returns tz-naive datetimes; normalize to UTC for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class TestTierResolution:
    def test_free_is_default(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        t = get_tier()
        assert t.name == "free"
        assert t.on_quota == "pause"
        assert t.allow_ensemble is False

    def test_paid_flips_capabilities_via_config_only(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_TIER", "paid")
        t = get_tier()
        assert t.name == "paid"
        assert t.on_quota == "fail"
        assert t.allow_ensemble is True
        assert t.max_rounds >= 2

    def test_daily_limit_tracks_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 123_456)
        assert get_tier().daily_token_limit == 123_456


class TestPauseRepository:
    async def test_pause_requeues_without_consuming_attempt(
        self, async_session, test_user
    ):
        job = await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
        )
        # simulate a claim (increments attempts to 1, status running)
        job.status = "running"
        job.attempts = 1
        await async_session.flush()

        resume_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        await jobs_repo.pause(
            async_session, job, available_at=resume_at, reason="quota exhausted"
        )
        await async_session.commit()
        await async_session.refresh(job)
        assert job.status == "queued"
        assert job.attempts == 0  # pause did NOT consume the retry
        assert _aware(job.available_at) >= datetime.now(timezone.utc)


class TestWorkerPauseResume:
    async def test_free_tier_pauses_then_resumes(
        self, async_session, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        _bind_sessions(monkeypatch, async_session)

        calls = {"n": 0}

        async def quota_then_ok(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise QuotaExhaustedError("429", reset_after=1)
            # second run (resume) succeeds

        import app.jobs.worker as worker_mod

        monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=quota_then_ok)})

        job = await jobs_repo.enqueue(
            async_session, kind="k", payload={"x": 1}, user_id=test_user.id
        )
        await async_session.commit()

        # First run: PAUSE (not fail) — re-queued for the future, attempt refunded.
        assert await _worker().run_once() is True
        await async_session.refresh(job)
        assert job.status == "queued"
        assert job.attempts == 0
        assert _aware(job.available_at) > datetime.now(timezone.utc)

        # Resume: make it available now; the worker completes it, no lost work.
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await async_session.commit()
        assert await _worker().run_once() is True
        await async_session.refresh(job)
        assert job.status == "succeeded"
        assert calls["n"] == 2

    async def test_paid_tier_fails_fast_on_quota(
        self, async_session, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "paid")
        _bind_sessions(monkeypatch, async_session)

        async def always_quota(payload):
            raise QuotaExhaustedError("429")

        import app.jobs.worker as worker_mod

        monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=always_quota)})

        job = await jobs_repo.enqueue(
            async_session,
            kind="k",
            payload={},
            user_id=test_user.id,
            max_attempts=1,
        )
        await async_session.commit()
        assert await _worker().run_once() is True
        await async_session.refresh(job)
        assert job.status == "dead"  # paid: a 429 is a real error


class TestClientQuotaClassification:
    async def test_repeated_429_raises_quota_exhausted(self, monkeypatch):
        import httpx
        from openai import RateLimitError

        from app.llm import client as client_mod
        from app.llm.client import LLMClient

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(settings, "LLM_TEMPERATURE_OVERRIDE", None)

        async def _no_sleep(*a, **k):
            return None

        monkeypatch.setattr(client_mod, "backoff_after_429", _no_sleep)

        client = LLMClient()
        rate_error = RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=httpx.Request("POST", "http://t")),
            body=None,
        )
        monkeypatch.setattr(
            client._client.chat.completions,
            "create",
            AsyncMock(side_effect=rate_error),
        )
        with pytest.raises(QuotaExhaustedError):
            await client.generate([{"role": "user", "content": "hi"}])


class TestBudgetPreflight:
    async def test_tokens_used_and_remaining(self, async_session, test_user):
        from app.core.orm_models import APIUsage

        async_session.add(
            APIUsage(user_id=test_user.id, endpoint="llm", tokens_used=1000)
        )
        await async_session.commit()

        used = await budget.tokens_used_24h(async_session, test_user.id)
        assert used == 1000

        # remaining respects the configured cap
        from app.core.config import settings as s

        remaining = await budget.remaining_budget_24h(async_session, test_user.id)
        assert remaining == max(0, s.USER_DAILY_TOKEN_LIMIT - 1000)

    def test_estimate_tokens(self):
        assert budget.estimate_tokens(4000) == 1000
        assert budget.estimate_tokens(0) == 1  # floor


class TestUsageEndpoint:
    async def test_usage_reports_tier_and_budget(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        r = await client.get("/api/v1/usage", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tier"] == "free"
        assert body["on_quota"] == "pause"
        assert body["allow_ensemble"] is False
        assert "tokens_remaining_24h" in body and "month_cost_usd" in body
