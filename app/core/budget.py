"""Per-user LLM budget enforcement + preflight estimation.

The deployment shares one free-tier LLM quota; the invite gate keeps strangers
out, this keeps any one user from burning the whole day's budget.

`check_user_budget` raises an HTTPException — correct at an enqueue endpoint,
wrong inside a background job. So the accounting is factored out into plain
`tokens_used_24h` / `remaining_budget_24h`, which the job layer can call to make
a preflight decision (degrade instead of half-writing a doomed multi-call job)
without a FastAPI dependency (docs/BRD.md R7.3).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .orm_models import APIUsage


async def tokens_used_24h(db: AsyncSession, user_id: UUID) -> int:
    """Tokens the user has spent in the rolling last 24h (no raising)."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return (
        await db.execute(
            select(func.coalesce(func.sum(APIUsage.tokens_used), 0)).where(
                APIUsage.user_id == user_id,
                APIUsage.timestamp >= since,
            )
        )
    ).scalar_one()


def _daily_limit() -> int:
    from app.llm.tier import get_tier

    return get_tier().daily_token_limit


async def _user_limit(user_id: UUID) -> int:
    """The author's daily cap: none for a Claude author (BRD R7.9), whom the
    monthly spend ceiling bounds instead; their thinking would spend a free-tier
    cap in a handful of scenes."""
    from app.llm.errors import QuotaExhaustedError
    from app.llm.providers import provider_for

    try:
        if (await provider_for(user_id)).kind == "anthropic":
            return 0
    except QuotaExhaustedError:  # the author unreadable: keep the tier's cap
        pass
    return _daily_limit()


async def deployment_cost_30d(db: AsyncSession) -> float:
    """Deployment-wide trailing-30-day spend (USD) — what the monthly ceiling
    is enforced against (all users share one provider bill)."""
    since = datetime.now(timezone.utc) - timedelta(days=30)
    value = (
        await db.execute(
            select(func.coalesce(func.sum(APIUsage.cost_usd), 0)).where(
                APIUsage.timestamp >= since
            )
        )
    ).scalar_one()
    return float(value or 0)


# check_monthly_ceiling runs on every LLM-spending request; a 30-day SUM over
# api_usage per click is wasted work for a number that moves by cents. A short
# TTL keeps the ceiling within seconds of exact (the usage page reads live).
_CEILING_TTL_SECONDS = 30.0
_ceiling_cache: dict[str, tuple[float, float]] = {}


async def _cached_deployment_cost_30d(db: AsyncSession) -> float:
    import time

    now = time.monotonic()
    hit = _ceiling_cache.get("spent")
    if hit is not None and now - hit[0] < _CEILING_TTL_SECONDS:
        return hit[1]
    spent = await deployment_cost_30d(db)
    _ceiling_cache["spent"] = (now, spent)
    return spent


async def check_monthly_ceiling(db: AsyncSession) -> None:
    """Raise 429 when the deployment's trailing-30-day spend has reached
    MONTHLY_COST_CEILING_USD (SPEC-03). A no-op when no ceiling is set."""
    from app.llm.tier import get_tier

    ceiling = get_tier().monthly_cost_ceiling_usd
    if not ceiling:
        return
    spent = await _cached_deployment_cost_30d(db)
    if spent >= ceiling:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"The monthly spending ceiling (${ceiling:.2f}) has been reached "
                f"(${spent:.2f} in the last 30 days). Generation resumes as the "
                "window rolls forward."
            ),
        )


async def remaining_budget_24h(db: AsyncSession, user_id: UUID) -> int:
    """Tokens the user may still spend today. A huge number when the cap is off."""
    limit = await _user_limit(user_id)
    if not limit:
        return 10**12  # effectively unlimited
    return max(0, limit - await tokens_used_24h(db, user_id))


def estimate_tokens(chars: int) -> int:
    """Rough token estimate for a body of text (~4 chars/token)."""
    return max(1, chars // 4)


async def check_user_budget(db: AsyncSession, user_id: UUID) -> None:
    """Raise 429 if the user has exhausted their rolling-24h token budget, or the
    deployment has reached its monthly spending ceiling."""
    await check_monthly_ceiling(db)
    limit = await _user_limit(user_id)
    if not limit:
        return
    used = await tokens_used_24h(db, user_id)
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily LLM token budget exhausted ({used}/{limit}). "
                "Try again later."
            ),
        )
