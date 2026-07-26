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
    max_rpm: int
    daily_token_limit: int
    on_quota: str  # 'pause' (free: re-queue and resume) | 'fail' (paid: real error)
    allow_staged_outline: bool
    allow_ensemble: bool
    max_agents: int
    max_rounds: int
    monthly_cost_ceiling_usd: float | None


# Free tier: pause and resume on quota; ensemble off (its RPD/token cost is not
# viable on the free tier — see docs/BRD.md §"Free-tier reality"). Single-call
# outline still allowed; the staged outline is gated on but degrades to a single
# call under budget pressure.
FREE = Tier(
    name="free",
    max_rpm=8,
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
)


def get_tier() -> Tier:
    """Resolve the active tier from config, with per-setting overrides.

    The daily token limit stays sourced from USER_DAILY_TOKEN_LIMIT so budget.py
    and the tier never disagree; the cost ceiling comes from
    MONTHLY_COST_CEILING_USD when set.
    """
    base = PAID if (settings.LLM_TIER or "free").lower() == "paid" else FREE
    return replace(
        base,
        daily_token_limit=settings.USER_DAILY_TOKEN_LIMIT,
        monthly_cost_ceiling_usd=settings.MONTHLY_COST_CEILING_USD,
    )
