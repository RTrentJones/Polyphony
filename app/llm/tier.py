"""Capability tiers — free-first, config-only graduation to paid (docs/BRD.md R7).

One frozen capability object read by budget, client, outline, and ensemble, so
graduating from free to paid is a single config flip (LLM_TIER=paid), never a
code change. The expensive features (staged outline, ensemble) are born
quota-safe because they ask the Tier what they're allowed to do.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.config import settings


@dataclass(frozen=True)
class Tier:
    name: str
    # Requests per minute; 0 = the provider row's own (free-tier) pacing.
    max_rpm: int
    daily_token_limit: int
    on_quota: str  # 'pause' (free: re-queue and resume) | 'fail' (paid: real error)
    allow_staged_outline: bool
    allow_ensemble: bool
    max_agents: int
    max_rounds: int
    monthly_cost_ceiling_usd: float | None
    # Enforce the provider's published requests-per-day ceiling locally
    # (app/llm/ledger.py). Free: yes — RPD is the binding limit. Paid: no.
    enforce_rpd: bool = True


# Free tier: pause and resume on quota; ensemble off (its RPD/token cost is not
# viable on the free tier — see docs/BRD.md §"Free-tier reality"). Single-call
# outline still allowed; the staged outline is gated on but degrades to a single
# call under budget pressure.
FREE = Tier(
    name="free",
    max_rpm=0,
    daily_token_limit=200_000,
    on_quota="pause",
    allow_staged_outline=True,
    allow_ensemble=False,
    max_agents=3,
    max_rounds=1,
    monthly_cost_ceiling_usd=None,
)

# Paid tier: a 429 is a real error (fail-fast), higher RPM/day, ensemble on with
# round 2. Provider swap is already free (ADR-001 §2's fungible registry).
PAID = Tier(
    name="paid",
    max_rpm=60,
    daily_token_limit=5_000_000,
    on_quota="fail",
    allow_staged_outline=True,
    allow_ensemble=True,
    max_agents=5,
    max_rounds=2,
    monthly_cost_ceiling_usd=None,
    enforce_rpd=False,
)


def get_tier() -> Tier:
    """Resolve the active tier from config.

    `LLM_TIER` alone decides pacing, the token cap, request-per-day tracking
    and the feature gates (BRD R7.6). Explicit per-setting overrides apply only
    when the override is actually set: `USER_DAILY_TOKEN_LIMIT` (tokens per user
    per rolling 24h; 0 disables the cap), `LLM_MAX_RPM`, `LLM_MAX_RPD`.
    """
    base = PAID if (settings.LLM_TIER or "free").lower() == "paid" else FREE
    overrides: dict = {"monthly_cost_ceiling_usd": settings.MONTHLY_COST_CEILING_USD}
    if settings.USER_DAILY_TOKEN_LIMIT is not None:
        overrides["daily_token_limit"] = settings.USER_DAILY_TOKEN_LIMIT
    if settings.LLM_MAX_RPM:
        overrides["max_rpm"] = settings.LLM_MAX_RPM
    return replace(base, **overrides)


def effective_rpm(provider_max_rpm: int) -> int:
    """Pacing for a provider under the active tier: an explicit LLM_MAX_RPM
    wins, then the tier's rate (paid), then the provider's free-tier default."""
    tier = get_tier()
    return tier.max_rpm or provider_max_rpm


def effective_rpd(provider, model: str) -> int | None:
    """The requests-per-day ceiling to enforce for one model, or None."""
    if settings.LLM_MAX_RPD:
        return settings.LLM_MAX_RPD
    if not get_tier().enforce_rpd:
        return None
    return provider.rpd.get(model)
