"""Per-user usage + tier visibility (docs/BRD.md R7.4).

Surfaces the active tier, the rolling-24h token budget, and 30-day spend so the
UI can show cost before an expensive click and so paid graduation is legible.
Reuses the accounting that already exists (api_usage + budget helpers).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
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
    month_cost = (
        await db.execute(
            select(func.coalesce(func.sum(APIUsage.cost_usd), 0)).where(
                APIUsage.user_id == current_user.id,
                APIUsage.timestamp >= since_30d,
            )
        )
    ).scalar_one()

    return {
        "tier": tier.name,
        "on_quota": tier.on_quota,
        "tokens_used_24h": used,
        "tokens_remaining_24h": remaining,
        "daily_token_limit": tier.daily_token_limit,
        "allow_ensemble": tier.allow_ensemble,
        "month_cost_usd": float(month_cost),
        "monthly_cost_ceiling_usd": tier.monthly_cost_ceiling_usd,
    }
