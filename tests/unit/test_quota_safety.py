"""SPEC-03: quota never kills a job, the tier owns its limits, and the
provider's requests-per-day ceiling is enforced before the call."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, RateLimitError
from sqlalchemy import select

from app.core.config import settings
from app.core.orm_models import Job
from app.jobs import repository as jobs_repo
from app.jobs.handlers import Handler
from app.jobs.worker import JobWorker
from app.llm import client as client_mod
from app.llm.errors import DailyRequestLimitError, QuotaExhaustedError

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


def _rate_error():
    return RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "http://t")),
        body=None,
    )


async def _no_sleep(*a, **k):
    return 0.0


class _NoPacer:
    """The real pacer enforces 8 RPM; these tests make dozens of calls."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_TEMPERATURE_OVERRIDE", None)
    monkeypatch.setattr(settings, "LLM_MAX_RPD", None)
    monkeypatch.setattr(client_mod, "backoff_after_429", _no_sleep)
    monkeypatch.setattr(client_mod, "get_pacer", lambda provider: _NoPacer())
    monkeypatch.setattr("app.llm.ledger._unavailable_until", 0.0)
    monkeypatch.setattr(settings, "LLM_TIER", "paid")  # no RPD ledger here
    c = client_mod.LLMClient()
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    return c


async def test_quota_storm_never_opens_the_breaker(llm, monkeypatch):
    monkeypatch.setattr(
        llm._client.chat.completions, "create", AsyncMock(side_effect=_rate_error())
    )
    monkeypatch.setattr(settings, "LLM_TIER", "paid")  # ledger off: pure 429s
    for _ in range(10):
        with pytest.raises(QuotaExhaustedError) as ei:
            await llm.generate([{"role": "user", "content": "hi"}])
        assert type(ei.value) is QuotaExhaustedError
    assert llm._breaker.state == "closed"


async def test_connection_storm_opens_breaker_then_pauses_not_fails(llm, monkeypatch):
    from app.llm.errors import ProviderUnavailableError

    err = APIConnectionError(request=httpx.Request("POST", "http://t"))
    monkeypatch.setattr(
        llm._client.chat.completions, "create", AsyncMock(side_effect=err)
    )
    monkeypatch.setattr(settings, "LLM_TIER", "paid")
    for _ in range(4):
        with pytest.raises(APIConnectionError):
            await llm.generate([{"role": "user", "content": "hi"}])
    assert llm._breaker.state == "open"
    with pytest.raises(ProviderUnavailableError) as ei:
        await llm.generate([{"role": "user", "content": "hi"}])
    assert ei.value.reset_after and ei.value.reset_after > 0


async def test_ten_quota_jobs_all_pause_none_die(async_session, test_user, monkeypatch):
    import app.jobs.handlers as handlers_mod
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(settings, "LLM_TIER", "free")

    async def run(payload):
        raise QuotaExhaustedError("429", reset_after=30)

    monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=run)})
    for _ in range(10):
        await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
        )
    await async_session.commit()
    w = JobWorker(poll_interval=0.01, stale_after=timedelta(minutes=30))
    for _ in range(10):
        assert await w.run_once() is True
    jobs = (await async_session.execute(select(Job))).scalars().all()
    assert all(j.status == "queued" for j in jobs)
    now = datetime.now(timezone.utc)
    assert all(j.available_at.replace(tzinfo=timezone.utc) > now for j in jobs)


async def test_provider_outage_pauses_even_on_paid(
    async_session, test_user, monkeypatch
):
    import app.jobs.handlers as handlers_mod
    import app.jobs.worker as worker_mod
    from app.llm.errors import ProviderUnavailableError

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(settings, "LLM_TIER", "paid")

    async def run(payload):
        raise ProviderUnavailableError("breaker open", reset_after=45)

    monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=run)})
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
    )
    await async_session.commit()
    w = JobWorker(poll_interval=0.01, stale_after=timedelta(minutes=30))
    await w.run_once()
    await async_session.refresh(job)
    assert job.status == "queued"


class TestTierOwnsLimits:
    def test_paid_flip_alone_moves_pacing_cap_and_gates(self, monkeypatch):
        from app.llm.providers import PROVIDERS
        from app.llm.tier import effective_rpd, effective_rpm, get_tier

        monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", None)
        monkeypatch.setattr(settings, "LLM_MAX_RPM", None)
        monkeypatch.setattr(settings, "LLM_MAX_RPD", None)
        gemini = PROVIDERS["gemini"]

        monkeypatch.setattr(settings, "LLM_TIER", "free")
        free = get_tier()
        assert effective_rpm(gemini.max_rpm) == gemini.max_rpm
        assert effective_rpd(gemini, fast=False) == gemini.max_rpd
        assert free.daily_token_limit == 200_000 and not free.allow_ensemble

        monkeypatch.setattr(settings, "LLM_TIER", "paid")
        paid = get_tier()
        assert effective_rpm(gemini.max_rpm) == 60
        assert effective_rpd(gemini, fast=False) is None
        assert paid.daily_token_limit == 5_000_000 and paid.allow_ensemble

    def test_explicit_overrides_still_win(self, monkeypatch):
        from app.llm.tier import get_tier

        monkeypatch.setattr(settings, "LLM_TIER", "paid")
        monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 1234)
        assert get_tier().daily_token_limit == 1234


class TestRequestLedger:
    async def test_ceiling_blocks_before_the_provider(self, async_session, monkeypatch):
        from app.llm import ledger

        monkeypatch.setattr(ledger, "_unavailable_until", 0.0)
        monkeypatch.setattr(
            "app.core.database.get_async_session", lambda: _Ctx(async_session)
        )
        for _ in range(3):
            await ledger.reserve("gemini", "m", 3, "America/Los_Angeles")
        with pytest.raises(DailyRequestLimitError) as ei:
            await ledger.reserve("gemini", "m", 3, "America/Los_Angeles")
        assert 60 <= ei.value.reset_after <= 86400
        # a different model has its own ceiling
        await ledger.reserve("gemini", "other", 3, "America/Los_Angeles")

    async def test_client_never_calls_provider_past_ceiling(
        self, llm, async_session, monkeypatch
    ):
        create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )
        )
        monkeypatch.setattr(llm._client.chat.completions, "create", create)
        monkeypatch.setattr(
            "app.core.database.get_async_session", lambda: _Ctx(async_session)
        )
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        monkeypatch.setattr(settings, "LLM_MAX_RPD", 2)
        monkeypatch.setattr(llm, "_record_usage", AsyncMock())
        await llm.generate([{"role": "user", "content": "a"}])
        await llm.generate([{"role": "user", "content": "b"}])
        with pytest.raises(DailyRequestLimitError):
            await llm.generate([{"role": "user", "content": "c"}])
        assert create.await_count == 2
        assert llm._breaker.state == "closed"


async def test_monthly_ceiling_is_enforced(async_session, test_user, monkeypatch):
    from fastapi import HTTPException

    from app.core import budget
    from app.core.orm_models import APIUsage

    monkeypatch.setattr(settings, "MONTHLY_COST_CEILING_USD", 1.0)
    async_session.add(
        APIUsage(user_id=test_user.id, endpoint="x", tokens_used=1, cost_usd=1.01)
    )
    await async_session.commit()
    with pytest.raises(HTTPException) as ei:
        await budget.check_user_budget(async_session, test_user.id)
    assert ei.value.status_code == 429 and "$1.00" in ei.value.detail


def test_thinking_guard_for_non_flash_gemini():
    from app.llm.client import _thinking_guard
    from app.llm.providers import PROVIDERS

    extra, mt = _thinking_guard(PROVIDERS["gemini"], "gemini-2.5-pro", 1000)
    assert not (extra or {}).get("reasoning_effort") and mt > 1000
    extra, mt = _thinking_guard(PROVIDERS["gemini"], "gemini-2.5-flash", 1000)
    assert extra["reasoning_effort"] == "none" and mt == 1000


def test_thinking_guard_keeps_groq_reasoning_effort_to_gpt_oss():
    """Groq rejects reasoning_effort on models without it (Codex review on #37):
    an LLM_MODEL / EVAL_JUDGE_MODEL override to another model drops it."""
    from app.llm.client import _thinking_guard
    from app.llm.providers import PROVIDERS

    extra, mt = _thinking_guard(PROVIDERS["groq"], "openai/gpt-oss-120b", 150)
    # reasoning counts against max_tokens: a small cap gets headroom
    assert extra == {"reasoning_effort": "low"} and mt == 150 + 1024
    extra, mt = _thinking_guard(PROVIDERS["groq"], "qwen/qwen3.8-27b", 1024)
    assert extra is None and mt == 1024


async def test_a_spent_author_budget_pauses_before_the_call(
    llm, async_session, test_user, monkeypatch
):
    """The token budget is enforced per call, not only at enqueue: a job that
    would spend past it pauses instead (Codex review on #34)."""
    from app.core.orm_models import APIUsage
    from app.llm.errors import UserBudgetExhaustedError

    monkeypatch.setattr(
        "app.core.database.get_async_session", lambda: _Ctx(async_session)
    )
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 1_000)
    create = AsyncMock(side_effect=AssertionError("called the provider"))
    monkeypatch.setattr(llm._client.chat.completions, "create", create)
    async_session.add(APIUsage(user_id=test_user.id, tokens_used=900, cost_usd=0))
    await async_session.commit()

    with pytest.raises(UserBudgetExhaustedError) as ei:
        await llm.generate(
            [{"role": "user", "content": "hi"}], user_id=test_user.id, max_tokens=500
        )
    assert ei.value.reset_after and ei.value.reset_after > 0
    create.assert_not_called()


async def test_the_budget_check_counts_reasoning_headroom(
    async_session, test_user, monkeypatch
):
    """A gpt-oss call is sent with max_tokens + headroom: the pre-call budget
    check must count that cap, not the caller's (Codex review on #37)."""
    from app.core.orm_models import APIUsage
    from app.llm.client import LLMClient
    from app.llm.errors import UserBudgetExhaustedError
    from app.llm.providers import PROVIDERS

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(
        "app.core.database.get_async_session", lambda: _Ctx(async_session)
    )
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 2_000)
    client = LLMClient(PROVIDERS["groq"])
    create = AsyncMock(side_effect=AssertionError("called the provider"))
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    async_session.add(APIUsage(user_id=test_user.id, tokens_used=900, cost_usd=0))
    await async_session.commit()

    # 150 tokens fit the 1,100 left; 150 + 1,024 of reasoning headroom do not.
    with pytest.raises(UserBudgetExhaustedError):
        await client.generate(
            [{"role": "user", "content": "hi"}], user_id=test_user.id, max_tokens=150
        )
    create.assert_not_called()


async def test_a_spent_author_budget_pauses_even_on_paid(
    async_session, test_user, monkeypatch
):
    import app.jobs.handlers as handlers_mod
    import app.jobs.worker as worker_mod
    from app.llm.errors import UserBudgetExhaustedError

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(settings, "LLM_TIER", "paid")

    async def run(payload):
        raise UserBudgetExhaustedError("spent", reset_after=60)

    monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=run)})
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
    )
    await async_session.commit()
    await JobWorker(poll_interval=0.01, stale_after=timedelta(minutes=30)).run_once()
    await async_session.refresh(job)
    assert job.status == "queued"


async def test_the_budget_check_counts_the_prompt_too(
    llm, async_session, test_user, monkeypatch
):
    """Input tokens are charged as well as output, so a call whose prompt
    would overrun the remaining budget pauses even with a small answer cap."""
    from app.core.orm_models import APIUsage
    from app.llm.errors import UserBudgetExhaustedError

    monkeypatch.setattr(
        "app.core.database.get_async_session", lambda: _Ctx(async_session)
    )
    monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 2_000)
    create = AsyncMock(side_effect=AssertionError("called the provider"))
    monkeypatch.setattr(llm._client.chat.completions, "create", create)
    async_session.add(APIUsage(user_id=test_user.id, tokens_used=1_000, cost_usd=0))
    await async_session.commit()

    window = "x" * 12_000  # ~3k prompt tokens against 1k remaining
    with pytest.raises(UserBudgetExhaustedError):
        await llm.generate(
            [{"role": "user", "content": window}], user_id=test_user.id, max_tokens=200
        )
    create.assert_not_called()


def test_an_unhinted_429_pauses_briefly_then_backs_off(monkeypatch):
    """A bare 429 is usually a per-minute limit: pause a minute, doubling per
    consecutive pause, never past QUOTA_PAUSE_SECONDS (the first live cel eval
    stalled for a flat 15 minutes on one)."""
    from app.jobs.worker import unhinted_pause_seconds

    monkeypatch.setattr(settings, "QUOTA_PAUSE_MIN_SECONDS", 60)
    monkeypatch.setattr(settings, "QUOTA_PAUSE_SECONDS", 900)
    assert [unhinted_pause_seconds(n) for n in range(6)] == [
        60,
        120,
        240,
        480,
        900,
        900,
    ]


async def test_a_job_paused_without_a_hint_resumes_in_a_minute(
    async_session, test_user, monkeypatch
):
    import app.jobs.handlers as handlers_mod
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    monkeypatch.setattr(settings, "QUOTA_PAUSE_MIN_SECONDS", 60)

    async def run(payload):
        raise QuotaExhaustedError("429")  # no retry-after

    monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=run)})
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
    )
    await async_session.commit()
    before = datetime.now(timezone.utc)
    await JobWorker(poll_interval=0.01, stale_after=timedelta(minutes=30)).run_once()
    await async_session.refresh(job)
    assert job.status == "queued"
    wait = job.available_at.replace(tzinfo=timezone.utc) - before
    assert timedelta(seconds=50) < wait < timedelta(seconds=90)
