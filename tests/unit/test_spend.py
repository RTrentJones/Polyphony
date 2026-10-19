"""SPEC-81: spend admission under a monthly ceiling, and the usage record.
Rows are numbered as in tpm-plan-wave12 §3 (E1, W12-1); rows 1-3 were cut and
row 12 folds into row 9 (principal ruling at the E1 checkpoint)."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError
from sqlalchemy import select

from app.core.config import settings
from app.core.orm_models import APIUsage
from app.jobs import repository as jobs_repo
from app.jobs.context import JobContext, job_context
from app.jobs.handlers import Handler
from app.llm import client as client_mod
from app.llm import spend
from app.llm.errors import MONTHLY_CEILING, PAUSE_REASON, SpendPausedError
from app.llm.providers import PROVIDERS
from tests.unit.test_job_lifecycle import _bind, _worker
from tests.unit.test_quota_safety import _Ctx, _no_sleep, _NoPacer

pytestmark = pytest.mark.unit
HI = [{"role": "user", "content": "hi"}]
WORST = 1024 * 10.0 / 1e6  # gpt-4o: a 1024-token cap, a 0-token prompt estimate


@pytest.fixture
def llm(monkeypatch, async_session):
    monkeypatch.setattr(spend, "_lock", asyncio.Lock())
    monkeypatch.setattr(spend, "_inflight", 0.0)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    for k, v in (("LLM_TEMPERATURE_OVERRIDE", None), ("LLM_MODEL", None)):
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "LLM_TIER", "paid")
    monkeypatch.setattr(settings, "MONTHLY_COST_CEILING_USD", 1.0)
    monkeypatch.setattr(client_mod, "backoff_after_429", _no_sleep)
    monkeypatch.setattr(client_mod, "get_pacer", lambda p: _NoPacer())
    db = lambda: _Ctx(async_session)  # noqa: E731
    monkeypatch.setattr("app.core.database.get_async_session", db)
    c = client_mod.LLMClient(PROVIDERS["openai"])
    reply = NS(message=NS(content="ok"))
    usage = NS(prompt_tokens=10, completion_tokens=20)
    c._client.chat.completions.create = AsyncMock(
        return_value=NS(choices=[reply], usage=usage)
    )
    return c


async def _job(session, user, monkeypatch, run, paused_h=0):
    _bind(monkeypatch, session)
    monkeypatch.setattr("app.jobs.worker.HANDLERS", {"k": Handler(run=run)})
    job = await jobs_repo.enqueue(session, kind="k", payload={}, user_id=user.id)
    if paused_h:
        job.paused_since = datetime.now(timezone.utc) - timedelta(hours=paused_h)
    await session.commit()
    await _worker().run_once()
    await session.refresh(job)
    return job


async def test_worker_pauses_fails_and_bounds(
    llm, async_session, test_user, monkeypatch
):
    # 4: the call that would cross the ceiling is never sent; the job pauses.
    row = APIUsage(endpoint="x", tokens_used=1, cost_usd=1 - WORST / 2)
    async_session.add(row)
    job = await _job(async_session, test_user, monkeypatch, lambda p: llm.generate(HI))
    assert llm._client.chat.completions.create.await_count == 0
    assert (job.status, job.error) == ("queued", MONTHLY_CEILING)
    # 4b: once the ledger is lower, the due job succeeds and its reason clears.
    row.cost_usd, job.available_at = 0.0, datetime.now(timezone.utc)
    await async_session.commit()
    await _worker().run_once()
    await async_session.refresh(job)
    assert (job.status, job.error) == ("succeeded", None)
    # 8: an unpriced model fails the job, naming the model and the fix.
    run = lambda p: llm.generate(HI, model="mystery-1")  # noqa: E731
    job = await _job(async_session, test_user, monkeypatch, run)
    assert (job.status, job.error) == (
        "dead",
        "'mystery-1' has no PRICES row; add " "one with source and date",
    )
    # 11: a ceiling pause past the 48 h bound fails, naming its reason.
    job = await _job(async_session, test_user, monkeypatch, _ceiling, paused_h=49)
    assert (job.status, job.error) == (
        "dead",
        "monthly ceiling reached; job stopped "
        "after 48h paused; re-running starts this job over",
    )


async def _ceiling(payload):
    raise SpendPausedError(MONTHLY_CEILING)


async def test_admit_is_exact_and_fails_closed(llm, monkeypatch):
    db = [1 - 1.5 * WORST]  # 6: B's row commits and B releases while A reads

    async def ledger(session):
        seen = db[0]
        await asyncio.sleep(0.01)
        return seen

    async def b_lands(hold):
        db[0] += hold
        await spend.release(hold)

    monkeypatch.setattr("app.core.budget.deployment_cost_30d", ledger)
    b = await spend.admit("gpt-4o", 0, 1024)
    with pytest.raises(SpendPausedError):  # B is in `spent` or `_inflight`
        await asyncio.gather(spend.admit("gpt-4o", 0, 1024), b_lands(b))
    # 7: an unreadable ledger pauses a priced call; a free model never reads it.
    monkeypatch.setattr("app.core.database.get_async_session", lambda: 1 / 0)
    with pytest.raises(SpendPausedError) as ei:
        await spend.admit("gpt-4o", 10, 10)
    assert ei.value.reason == "accounting_unavailable"
    assert await spend.admit("stub-default", 10, 10) == 0


async def test_client_holds_records_and_reports(
    llm, async_session, test_user, monkeypatch, client, auth_headers
):
    # 5: an error before a response releases; a failed record keeps holding.
    create = llm._client.chat.completions.create
    create.side_effect = APIConnectionError(request=httpx.Request("POST", "http://t"))
    with pytest.raises(APIConnectionError):
        await llm.generate(HI)
    assert spend._inflight == 0
    create.side_effect = None
    with monkeypatch.context() as m:
        m.setattr(llm, "_record_usage", AsyncMock(return_value=False))
        await llm.generate(HI)
    assert spend._inflight == pytest.approx(WORST)
    # 9 (F) + 12: with no ceiling nothing is enforced, and every response is
    # recorded with its job, user or not; /usage keeps unpriced tokens apart.
    monkeypatch.setattr(settings, "MONTHLY_COST_CEILING_USD", None)
    u = test_user.id
    with job_context(JobContext(job_id=u)):
        for model, who in (("gpt-4o", u), ("mystery-1", u), ("stub-default", None)):
            await llm.generate(HI, model=model, user_id=who)
    q = select(APIUsage.model, APIUsage.cost_usd, APIUsage.user_id, APIUsage.job_id)
    got = (await async_session.execute(q)).all()
    rows = {m: (None if c is None else float(c), w, j) for m, c, w, j in got}
    want = {"gpt-4o": 0.000225, "mystery-1": None, "stub-default": 0.0}
    assert rows == {
        m: (c, None if m == "stub-default" else u, u) for m, c in want.items()
    }
    body = (await client.get("/api/v1/usage", headers=auth_headers)).json()
    assert (body["month_cost_usd"], body["unpriced_tokens_30d"]) == (0.000225, 30)


async def test_pause_seconds_accumulate_by_reason(async_session, test_user):  # 10
    u, now = test_user.id, datetime.now(timezone.utc)
    job = await jobs_repo.enqueue(async_session, kind="k", payload={}, user_id=u)
    for s, why in ((60, MONTHLY_CEILING), (120, MONTHLY_CEILING), (30, PAUSE_REASON)):
        at = now + timedelta(seconds=s)
        await jobs_repo.pause(async_session, job, available_at=at, reason=why)
    want = {MONTHLY_CEILING: 180, PAUSE_REASON: 30}
    assert job.pause_seconds == pytest.approx(want, abs=1)
