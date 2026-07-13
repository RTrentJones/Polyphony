"""Unit tests for the provider-fungible LLM layer."""

import asyncio
import time

import pytest

from app.llm.providers import (
    PROVIDERS,
    active_provider,
    cost_usd,
    enabled_providers,
    provider_api_key,
    resolve_model,
)
from app.llm.pacing import RateLimiter


@pytest.mark.unit
class TestProviderRegistry:
    def test_registry_has_expected_providers(self):
        assert {
            "gemini",
            "groq",
            "xai",
            "openai",
            "cerebras",
            "openrouter",
            "mistral",
        } <= set(PROVIDERS)

    def test_registry_rows_are_well_formed(self):
        # Every openai-kind row (except openai itself) needs a base_url, and
        # env keys must be unique — a duplicate would silently share quota.
        env_keys = [p.env_key for p in PROVIDERS.values()]
        assert len(env_keys) == len(set(env_keys))
        for p in PROVIDERS.values():
            assert p.kind in ("openai", "anthropic")
            if p.kind == "openai" and p.id != "openai":
                assert p.base_url, f"{p.id} missing base_url"
            assert p.default_model and p.fast_model
            assert p.max_rpm >= 1

    def test_gemini_is_openai_compatible(self):
        gemini = PROVIDERS["gemini"]
        assert gemini.kind == "openai"
        assert gemini.base_url is not None
        assert "generativelanguage.googleapis.com" in gemini.base_url
        assert gemini.env_key == "GEMINI_API_KEY"

    def test_groq_default_model_is_live(self):
        # The old default (llama-3.1-70b-versatile) was decommissioned on Groq.
        assert PROVIDERS["groq"].default_model == "llama-3.3-70b-versatile"

    def test_default_provider_is_gemini(self):
        assert active_provider().id == "gemini"

    def test_env_key_gating(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        enabled = {p.id for p in enabled_providers()}
        assert "groq" in enabled
        assert "gemini" not in enabled
        assert provider_api_key(PROVIDERS["gemini"]) is None

    def test_resolve_model_falls_back_to_registry(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "LLM_MODEL", None)
        monkeypatch.setattr(settings, "LLM_MODEL_FAST", None)
        gemini = PROVIDERS["gemini"]
        assert resolve_model(gemini, fast=False) == gemini.default_model
        assert resolve_model(gemini, fast=True) == gemini.fast_model

    def test_resolve_model_env_override(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "LLM_MODEL", "gemini-2.5-pro")
        assert resolve_model(PROVIDERS["gemini"], fast=False) == "gemini-2.5-pro"

    def test_cost_free_tier_is_zero(self):
        assert cost_usd(PROVIDERS["gemini"], 100_000, 100_000) == 0.0

    def test_cost_paid_tier(self):
        cost = cost_usd(PROVIDERS["openai"], 1_000_000, 1_000_000)
        assert cost == pytest.approx(12.5)


@pytest.mark.unit
class TestRateLimiter:
    def test_burst_within_budget_is_immediate(self):
        async def run():
            limiter = RateLimiter(rpm=60)
            start = time.monotonic()
            for _ in range(5):
                await limiter.acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(run())
        assert elapsed < 0.5  # burst capacity, no waiting

    def test_exhausted_bucket_waits(self):
        async def run():
            limiter = RateLimiter(rpm=60)  # 1 token/second refill
            for _ in range(60):
                await limiter.acquire()  # drain the burst
            start = time.monotonic()
            await limiter.acquire()  # must wait ~1s for a refill
            return time.monotonic() - start

        elapsed = asyncio.run(run())
        assert elapsed >= 0.5
