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
