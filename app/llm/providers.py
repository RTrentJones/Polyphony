"""Vendor-agnostic LLM provider registry.

Modeled on RTrentJones.dev/tools/tracer/lib/providers.ts: most vendors expose
an OpenAI-compatible endpoint, so one `openai`-SDK adapter parameterized by
base_url covers Gemini / Groq / xAI / OpenAI. Each provider is gated on its own
env key. Add a vendor by appending a row.
"""

import os
from dataclasses import dataclass, field
from typing import Literal, Optional

from app.core.config import settings


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: Literal["openai", "anthropic"]
    env_key: str  # env var holding the API key
    base_url: Optional[str]  # openai-compatible endpoint (kind: "openai")
    default_model: str
    fast_model: str
    # USD per 1M tokens (best-effort, for cost display; 0 for free tiers)
    rate_in: float = 0.0
    rate_out: float = 0.0
    # Free-tier requests-per-minute pacing default (overridable via LLM_MAX_RPM)
    max_rpm: int = 10
    # Extra JSON body fields for chat.completions (provider-specific knobs).
    extra_body: Optional[dict] = None


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        id="gemini",
        label="Google Gemini",
        kind="openai",
        env_key="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-2.5-flash",
        fast_model="gemini-2.5-flash-lite",
        max_rpm=8,  # AI Studio free tier is ~10 RPM; leave headroom
        # 2.5 models are thinking models on the OpenAI-compat endpoint, and
        # thinking tokens count against max_tokens — small caps starve the
        # actual output. This app's calls are structured/functional, so turn
        # thinking off (supported on flash/flash-lite).
        extra_body={"reasoning_effort": "none"},
    ),
    "groq": Provider(
        id="groq",
        label="Groq",
        kind="openai",
        env_key="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        fast_model="llama-3.1-8b-instant",
        max_rpm=25,
    ),
    "xai": Provider(
        id="xai",
        label="xAI Grok",
        kind="openai",
        env_key="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
        default_model="grok-4",
        fast_model="grok-4",
        max_rpm=30,
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        kind="openai",
        env_key="OPENAI_API_KEY",
        base_url=None,
        default_model="gpt-4o",
        fast_model="gpt-4o-mini",
        rate_in=2.5,
        rate_out=10.0,
        max_rpm=60,
    ),
}


@dataclass
class GenResult:
    text: str
    tokens_in: int
    tokens_out: int
    provider: str
    model: str
    latency_ms: int
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


def active_provider() -> Provider:
    """The provider selected by LLM_PROVIDER."""
    provider_id = settings.LLM_PROVIDER
    if provider_id not in PROVIDERS:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider_id}' (known: {sorted(PROVIDERS)})"
        )
    return PROVIDERS[provider_id]


def provider_api_key(provider: Provider) -> Optional[str]:
    """The provider's API key from its own env var, or None when absent."""
    return os.getenv(provider.env_key) or None


def enabled_providers() -> list[Provider]:
    """Providers whose API key is present in the environment."""
    return [p for p in PROVIDERS.values() if provider_api_key(p)]


def resolve_model(provider: Provider, fast: bool) -> str:
    """The configured model, falling back to the provider's registry default."""
    if fast:
        return settings.LLM_MODEL_FAST or provider.fast_model
    return settings.LLM_MODEL or provider.default_model


def cost_usd(provider: Provider, tokens_in: int, tokens_out: int) -> float:
    return (tokens_in * provider.rate_in + tokens_out * provider.rate_out) / 1_000_000
