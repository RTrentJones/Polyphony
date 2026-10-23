"""Per-user usage + tier visibility (docs/BRD.md R7.4).

Surfaces the active tier, the rolling-24h token budget, and 30-day spend so the
UI can show cost before an expensive click and so paid graduation is legible.
Reuses the accounting that already exists (api_usage + budget helpers).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import budget
from app.core.database import get_db
from app.core.orm_models import APIUsage, User as UserORM
from app.core.security import get_current_active_user
from app.llm.tier import get_tier

router = APIRouter()


@router.get("/usage", response_model=dict)
async def get_usage(
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The current user's tier, rolling-24h token budget, and 30-day spend."""
    tier = get_tier()
    used = await budget.tokens_used_24h(db, current_user.id)
    remaining = await budget.remaining_budget_24h(db, current_user.id)

    since_30d = datetime.now(timezone.utc) - timedelta(days=30)
    # Unpriced calls (cost IS NULL, SPEC-81): their tokens beside the cost.
    unpriced = case((APIUsage.cost_usd.is_(None), APIUsage.tokens_used), else_=0)
    month_cost, unpriced_tokens = (
        await db.execute(
            select(
                func.coalesce(func.sum(APIUsage.cost_usd), 0),
                func.coalesce(func.sum(unpriced), 0),
            ).where(
                APIUsage.user_id == current_user.id,
                APIUsage.timestamp >= since_30d,
            )
        )
    ).one()

    from app.core.estimates import requests_left_today
    from app.llm.providers import provider_for, selectable
    from app.llm.tier import effective_rpd

    provider = await provider_for(current_user.id)
    by_purpose = (
        await db.execute(
            select(
                APIUsage.endpoint,
                func.coalesce(func.sum(APIUsage.tokens_used), 0),
                func.coalesce(func.sum(APIUsage.cost_usd), 0),
                func.count(APIUsage.id),
            )
            .where(
                APIUsage.user_id == current_user.id,
                APIUsage.timestamp >= since_30d,
            )
            .group_by(APIUsage.endpoint)
        )
    ).all()
    deployment_30d = await budget.deployment_cost_30d(db)

    return {
        # Today's requests per model (U1): the running default first, then rpd's.
        "models": {
            m: {
                "left": await requests_left_today(m, provider),
                "per_day": effective_rpd(provider, m),
            }
            for m in selectable(provider)
        },
        "by_purpose_30d": [
            {
                "purpose": purpose or "llm",
                "tokens": int(tokens or 0),
                "usd": float(cost or 0),
                "calls": int(calls or 0),
            }
            for purpose, tokens, cost, calls in sorted(
                by_purpose, key=lambda r: -(r[1] or 0)
            )
        ],
        "deployment_cost_30d_usd": deployment_30d,
        "tier": tier.name,
        "on_quota": tier.on_quota,
        "tokens_used_24h": used,
        "tokens_remaining_24h": remaining,
        "daily_token_limit": tier.daily_token_limit,
        "allow_ensemble": tier.allow_ensemble,
        "month_cost_usd": float(month_cost),
        "unpriced_tokens_30d": int(unpriced_tokens),
        "monthly_cost_ceiling_usd": tier.monthly_cost_ceiling_usd,
    }
