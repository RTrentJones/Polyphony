"""Unit tests for the LLM client's eval-determinism temperature override."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.client import LLMClient

pytestmark = pytest.mark.unit


def _fake_response():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


async def _capture_temperature(monkeypatch, override):
    from app.core.config import settings

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_TEMPERATURE_OVERRIDE", override)
    client = LLMClient()
    create = AsyncMock(return_value=_fake_response())
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    await client.generate([{"role": "user", "content": "hi"}], temperature=0.9)
    return create.call_args.kwargs["temperature"]


async def test_override_pins_temperature(monkeypatch):
    # With the override set, the passed temperature (0.9) is ignored.
    assert await _capture_temperature(monkeypatch, 0.0) == 0.0


async def test_no_override_uses_passed_temperature(monkeypatch):
    # Off by default → production sampling is untouched.
    assert await _capture_temperature(monkeypatch, None) == 0.9


async def test_failed_generate_is_one_breaker_failure(monkeypatch):
    # Regression: the breaker wraps the whole retry sequence, so a single
    # generate() that exhausts _MAX_ATTEMPTS retries must count as ONE breaker
    # failure — not _MAX_ATTEMPTS — otherwise one unlucky call trips the breaker
    # (threshold == _MAX_ATTEMPTS) and blackholes every later call. Previously
    # this cascaded the eval's whole generation half to CircuitBreakerError.
    import httpx
    from openai import APITimeoutError

    from app.core.config import settings
    from app.llm import client as client_mod

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_TEMPERATURE_OVERRIDE", None)

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(client_mod, "backoff_after_429", _no_sleep)

    client = LLMClient()
    create = AsyncMock(
        side_effect=APITimeoutError(request=httpx.Request("POST", "http://t"))
    )
    monkeypatch.setattr(client._client.chat.completions, "create", create)

    with pytest.raises(APITimeoutError):
        await client.generate([{"role": "user", "content": "hi"}])

    assert create.await_count == 4  # all retries ran
    assert client._breaker.failure_count == 1  # but the breaker saw one failure
    assert client._breaker.is_open is False  # so it stays closed
