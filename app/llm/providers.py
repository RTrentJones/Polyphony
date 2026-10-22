"""Vendor-agnostic LLM provider registry.

Modeled on RTrentJones.dev/tools/tracer/lib/providers.ts: most vendors expose
an OpenAI-compatible endpoint, so one `openai`-SDK adapter parameterized by
base_url covers Gemini / Groq / xAI / OpenAI. Each provider is gated on its own
env key. Add a vendor by appending a row.
"""

import os
from dataclasses import dataclass, field
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AfterValidator, Field

from app.core.config import settings
from app.jobs.context import current_job


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: Literal["openai", "anthropic"]
    env_key: str  # env var holding the API key
    base_url: Optional[str]  # openai-compatible endpoint (kind: "openai")
    default_model: str
    fast_model: str
    # Free-tier requests-per-minute pacing default (overridable via LLM_MAX_RPM)
    max_rpm: int = 10
    # Free-tier requests-per-DAY ceiling per model (absent = not tracked); the
    # binding free-tier limit (docs/BRD.md §6.1), enforced by app/llm/ledger.py,
    # overridable via LLM_MAX_RPD. Its keys are the models an author may choose
    # for a scene, default first (U1): one table for "selectable" and "how many".
    # None = selectable, with no daily ceiling to track (a paid provider).
    rpd: dict[str, Optional[int]] = field(default_factory=dict)
    # The IANA zone the vendor's daily quota resets in.
    rpd_reset_tz: str = "UTC"
    # Extra JSON body fields for chat.completions (provider-specific knobs).
    extra_body: Optional[dict] = None


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        id="gemini",
        label="Google Gemini",
        kind="openai",
        env_key="GEMINI_API_KEY",
        base_url=os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),  # fmt: skip
        # The 2.5 models are served only to projects that already used them
        # (models page, re-checked 2026-10-16); a new key gets a 404.
        default_model="gemini-3.5-flash-lite",  # U1: the whole pipeline
        fast_model="gemini-3.5-flash-lite",
        max_rpm=8,  # AI Studio free tier is ~10 RPM; leave headroom
        # Google no longer publishes a universal free-tier table: limits are
        # per project and shown in AI Studio (re-checked 2026-10). The reset at
        # midnight Pacific is documented. Both figures are measured quotaValues;
        # set LLM_MAX_RPD to the project's AI Studio value to override.
        rpd={
            "gemini-3.5-flash-lite": 500,  # quotaValue, run 39495277558, 2026-10-20
            "gemini-3.6-flash": 20,  # quotaValue, W13-0 probe 2026-10-20 (opt-in)
        },
        rpd_reset_tz="America/Los_Angeles",
        # Gemini 3 models cannot turn thinking off, and thinking tokens count
        # against max_tokens. This app's calls are structured, so ask for the
        # least thinking both models accept ("none" is 2.5-only, and 3.8 Flash
        # bottoms out at "low"); _thinking_guard adds headroom for it.
        extra_body={"reasoning_effort": "minimal"},
    ),
    "groq": Provider(
        id="groq",
        label="Groq",
        kind="openai",
        env_key="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        # Groq retired the Llama 3.x models on 2026-08-16; these are its named
        # replacements. gpt-oss is a reasoning model whose reasoning counts
        # against max_tokens, so keep its effort low for short structured replies.
        default_model="openai/gpt-oss-120b",
        fast_model="openai/gpt-oss-20b",
        max_rpm=25,
        extra_body={"reasoning_effort": "low"},
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
        max_rpm=60,
    ),
    # Paid, and only for the authors named in ANTHROPIC_USERS (provider_for):
    # everyone else stays on LLM_PROVIDER. The native SDK (kind "anthropic",
    # app/llm/client.py), priced in app/llm/prices.py so the monthly ceiling
    # holds it.
    "anthropic": Provider(
        id="anthropic",
        label="Anthropic Claude",
        kind="anthropic",
        env_key="ANTHROPIC_API_KEY",
        base_url=None,
        default_model="claude-opus-5-5",
        fast_model="claude-sonnet-5-5",
        max_rpm=50,
        rpd={"claude-opus-5-5": None, "claude-sonnet-5-5": None},
    ),
    # --- additional free-tier vendors (all OpenAI-compatible) ---------------
    # Primary use today: the EVAL JUDGE (EVAL_JUDGE_PROVIDER) — a judge from a
    # different vendor than the model under test removes self-preference bias
    # AND takes judge calls off the shared Gemini daily budget. Any row can
    # also serve as the app's LLM_PROVIDER.
    "cerebras": Provider(
        id="cerebras",
        label="Cerebras",
        kind="openai",
        env_key="CEREBRAS_API_KEY",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
        fast_model="llama3.1-8b",
        max_rpm=25,  # free tier ~30 RPM; leave headroom
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter",
        kind="openai",
        env_key="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        # :free routes cost nothing; free daily request caps are modest —
        # fine for judge duty, not for bulk generation. The free Llama routes
        # were withdrawn (2026-10); the free list changes, so check
        # https://openrouter.ai/api/v1/models when a judge call 404s.
        default_model="google/gemma-4-31b-it:free",
        fast_model="google/gemma-4-26b-a4b-it:free",
        max_rpm=15,
    ),
    # PRIVACY: Mistral's free Experiment tier trains on submitted prompts.
    # Acceptable for the eval judge (renamed public-domain corpus text only);
    # do NOT make it the app's LLM_PROVIDER — user manuscripts would flow into
    # third-party training data.
    "mistral": Provider(
        id="mistral",
        label="Mistral",
        kind="openai",
        env_key="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
        default_model="mistral-small-latest",
        fast_model="mistral-small-latest",
        max_rpm=30,  # free Experiment plan is ~1 rps
    ),
    # --- local deterministic stub (evals/stub_llm.py) -----------------------
    # For end-to-end tests and local previews with no key, no quota, no cost.
    # Gated like every row on its own env key, so it is inert in production.
    "stub": Provider(
        id="stub",
        label="Stub (local tests)",
        kind="openai",
        env_key="STUB_LLM_API_KEY",
        base_url=os.getenv("STUB_LLM_BASE_URL", "http://127.0.0.1:8099/v1"),
        default_model="stub-default",
        fast_model="stub-fast",
        max_rpm=600,
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
    cost_usd: Optional[float]  # None = unpriced (app/llm/prices.py)
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


def _configured(provider: Provider, fast: bool) -> str:
    """LLM_MODEL(_FAST) for the deployment's provider, else the registry default."""
    if provider.id != settings.LLM_PROVIDER:
        return provider.fast_model if fast else provider.default_model
    if fast:
        return settings.LLM_MODEL_FAST or provider.fast_model
    return settings.LLM_MODEL or provider.default_model


def resolve_model(provider: Provider, fast: bool) -> str:
    """The running job's chosen model (U1) when this provider serves it, else
    the configured model. The ONE read of a job's model."""
    chosen = None if fast else current_job().model
    return chosen if chosen in selectable(provider) else _configured(provider, fast)


def selectable(provider: Provider) -> list[str]:
    """The models an author may choose (U1): the configured default first, then rpd's."""
    return list(dict.fromkeys([_configured(provider, False), *provider.rpd]))


def _job_model(model: Optional[str]) -> str:
    """A scene-writing request's model (U1): one any enabled provider offers
    (else 422), or the default when none is chosen. Stamped into the job payload
    as given; the author's own provider (provider_for) serves it, and a model it
    does not offer falls back to that provider's default (resolve_model)."""
    providers = [
        active_provider(),
        *(p for p in enabled_providers() if p.kind == "anthropic"),
    ]
    allowed = list(dict.fromkeys(m for p in providers for m in selectable(p)))
    if model is not None and model not in allowed:
        raise ValueError(f"unknown model; choose one of {allowed}")
    return model or resolve_model(active_provider(), fast=False)


_emails: dict[UUID, str] = {}  # user id -> email; an email does not change


async def provider_for(user_id: Optional[UUID]) -> Provider:
    """The author's provider: Anthropic for an ANTHROPIC_USERS email when its
    key is set, else the deployment's LLM_PROVIDER."""
    raw = os.getenv("ANTHROPIC_USERS", "")  # comma-separated author emails
    users = {e.strip().lower() for e in raw.split(",") if e.strip()}
    anthropic = PROVIDERS["anthropic"]
    if user_id is None or not users or not provider_api_key(anthropic):
        return active_provider()
    if user_id not in _emails:
        from app.core.database import get_async_session
        from app.core.orm_models import User

        async with get_async_session() as session:
            user = await session.get(User, user_id)
        if user is None:
            return active_provider()
        _emails[user_id] = user.email.lower()
    return anthropic if _emails[user_id] in users else active_provider()


ModelChoice = Annotated[
    Optional[str], AfterValidator(_job_model), Field(validate_default=True)
]
