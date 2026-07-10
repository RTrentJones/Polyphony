"""Per-user LLM budget enforcement.

The deployment shares one free-tier LLM quota; the invite gate keeps strangers
out, this keeps any one user from burning the whole day's budget.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .orm_models import APIUsage


async def check_user_budget(db: AsyncSession, user_id: UUID) -> None:
    """Raise 429 if the user has exhausted their rolling-24h token budget."""
    limit = settings.USER_DAILY_TOKEN_LIMIT
    if not limit:
        return
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    used = (
        await db.execute(
            select(func.coalesce(func.sum(APIUsage.tokens_used), 0)).where(
                APIUsage.user_id == user_id,
                APIUsage.timestamp >= since,
            )
        )
    ).scalar_one()
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily LLM token budget exhausted ({used}/{limit}). "
                "Try again later."
            ),
        )
