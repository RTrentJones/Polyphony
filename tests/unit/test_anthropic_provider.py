"""The Anthropic provider: per-author routing (provider_for), model resolution
across providers, and the native Messages call's shape, refusals and cost."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.jobs.context import JobContext, job_context
from app.llm import client as llm_client
from app.llm import providers
from app.llm.errors import (
    ProviderRefusalError,
    ProviderUnavailableError,
    TruncatedReplyError,
)
from app.llm.prices import cost_usd

pytestmark = pytest.mark.unit

CLAUDE = providers.PROVIDERS["anthropic"]
MSGS = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]


@pytest.fixture
def listed(monkeypatch):
    """One author listed in ANTHROPIC_USERS, with the key set."""
    user = uuid4()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_USERS", " Me@Example.com ,you@example.com")
    monkeypatch.setitem(providers._emails, user, "me@example.com")
    return user


async def test_only_a_listed_author_with_the_key_gets_claude(listed, monkeypatch):
    other = uuid4()
    monkeypatch.setitem(providers._emails, other, "them@example.com")
    assert (await providers.provider_for(listed)).id == "anthropic"
    assert (await providers.provider_for(other)).id == settings.LLM_PROVIDER
    assert (await providers.provider_for(None)).id == settings.LLM_PROVIDER
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert (await providers.provider_for(listed)).id == settings.LLM_PROVIDER


def test_a_job_model_from_another_provider_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODEL", "gemini-3.6-flash")
    with job_context(JobContext(model="gemini-3.6-flash")):
        assert providers.resolve_model(CLAUDE, False) == "claude-opus-5-5"
    with job_context(JobContext(model="claude-sonnet-5-5")):
        assert providers.resolve_model(CLAUDE, False) == "claude-sonnet-5-5"
        gemini = providers.PROVIDERS["gemini"]
        assert providers.resolve_model(gemini, False) == "gemini-3.6-flash"
    assert providers.selectable(CLAUDE) == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_a_claude_model_is_choosable_only_with_the_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="unknown model"):
        providers._job_model("claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert providers._job_model("claude-opus-5-5") == "claude-opus-5-5"


def _usage(model=None, n_in=100, n_out=50, **more):
    return SimpleNamespace(
        model=model,
        input_tokens=n_in,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=20,
        output_tokens=n_out,
        **more,
    )


def _reply(stop_reason="end_turn", text='{"ok": 1}', usage=None):
    usage = usage or _usage()
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber"),
        content=[
            SimpleNamespace(type="thinking"),
            SimpleNamespace(type="text", text=text),
        ],
        usage=usage,
    )


async def _claude(listed, monkeypatch, reply):
    monkeypatch.setattr(settings, "LLM_TEMPERATURE_OVERRIDE", None)
    monkeypatch.setenv(providers.active_provider().env_key, "test-key")
    client = llm_client.LLMClient(CLAUDE)
    create = AsyncMock(side_effect=[reply] if not isinstance(reply, list) else reply)
    monkeypatch.setattr(client._client.beta.messages, "create", create)
    monkeypatch.setattr(client, "_record_usage", AsyncMock(return_value=True))
    router = llm_client.LLMRouter()  # startup logs its .provider (app/main.py)
    assert router.provider.id == settings.LLM_PROVIDER
    router._clients["anthropic"] = client
    return router, create


async def test_the_messages_call_lifts_system_and_sends_no_temperature(
    listed, monkeypatch
):
    router, create = await _claude(listed, monkeypatch, _reply())
    result = await router.generate(MSGS, user_id=listed, max_tokens=640)
    sent = create.call_args.kwargs
    assert sent["system"] == "Be brief."
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert "temperature" not in sent and sent["model"] == "claude-opus-5-5"
    assert sent["max_tokens"] == 640 + llm_client._ANTHROPIC_THINKING
    assert sent["fallbacks"] == "default"
    assert (result.text, result.tokens_in, result.tokens_out) == ('{"ok": 1}', 120, 50)
    assert result.cost_usd == cost_usd("claude-opus-5-5", 120, 50) == 0.00148


async def test_a_refusal_fails_once_without_a_retry(listed, monkeypatch):
    router, create = await _claude(listed, monkeypatch, _reply("refusal", ""))
    with pytest.raises(ProviderRefusalError, match="cyber"):
        await router.generate(MSGS, user_id=listed)
    assert create.await_count == 1


async def test_an_empty_truncated_reply_fails_and_a_fallback_bills_each_model(
    listed, monkeypatch
):
    router, _ = await _claude(listed, monkeypatch, _reply("max_tokens", ""))
    with pytest.raises(TruncatedReplyError):
        await router.generate(MSGS, user_id=listed)
    runs = [_usage("claude-opus-5-5", 100, 10), _usage("claude-opus-4-8", 100, 50)]
    router, _ = await _claude(
        listed, monkeypatch, _reply(usage=_usage(iterations=runs))
    )
    result = await router.generate(MSGS, user_id=listed)
    assert (result.tokens_in, result.tokens_out) == (240, 60)
    assert result.cost_usd == pytest.approx(
        cost_usd("claude-opus-5-5", 120, 10) + cost_usd("claude-opus-4-8", 120, 50)
    )


async def test_overload_pauses_and_an_unreadable_author_pauses(listed, monkeypatch):
    import anthropic
    import httpx2

    monkeypatch.setattr(settings, "LLM_MAX_RETRY_WAIT_SECONDS", 0)
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded = anthropic.OverloadedError(
        "overloaded", response=httpx2.Response(529, request=req), body=None
    )
    router, _ = await _claude(listed, monkeypatch, [overloaded])
    with pytest.raises(ProviderUnavailableError):
        await router.generate(MSGS, user_id=listed)

    def no_db():
        raise OSError("db down")

    monkeypatch.setattr("app.core.database.get_async_session", no_db)
    with pytest.raises(ProviderUnavailableError, match="author"):
        await providers.provider_for(uuid4())


async def test_a_claude_author_has_no_daily_cap_and_no_gemini_rpd(listed, monkeypatch):
    from app.core import budget
    from app.llm.tier import effective_rpd

    assert await budget._user_limit(listed) == 0
    monkeypatch.setattr(settings, "LLM_MAX_RPD", 10)
    assert effective_rpd(CLAUDE, "claude-opus-5-5") is None
