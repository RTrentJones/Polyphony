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

from .config import settings
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


async def remaining_budget_24h(db: AsyncSession, user_id: UUID) -> int:
    """Tokens the user may still spend today. A huge number when the cap is off."""
    limit = settings.USER_DAILY_TOKEN_LIMIT
    if not limit:
        return 10**12  # effectively unlimited
    return max(0, limit - await tokens_used_24h(db, user_id))


def estimate_tokens(chars: int) -> int:
    """Rough token estimate for a body of text (~4 chars/token)."""
    return max(1, chars // 4)


async def check_user_budget(db: AsyncSession, user_id: UUID) -> None:
    """Raise 429 if the user has exhausted their rolling-24h token budget."""
    limit = settings.USER_DAILY_TOKEN_LIMIT
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
