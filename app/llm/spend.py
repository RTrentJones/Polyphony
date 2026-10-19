"""Spend admission under MONTHLY_COST_CEILING_USD (SPEC-81): read, check and hold
in one critical section, so concurrent callers never jointly cross the ceiling."""

import asyncio

from app.core.logging_config import setup_logging

from . import errors
from .prices import PRICES

logger = setup_logging("llm.spend")
_lock, _inflight = asyncio.Lock(), 0.0


async def admit(model: str, est_in: int, cap: int) -> float | None:
    """Hold the call's worst case (USD) or raise; None when no ceiling is set."""
    global _inflight
    from app.core.budget import deployment_cost_30d
    from app.core.database import get_async_session
    from app.llm.tier import get_tier

    if not (ceiling := get_tier().monthly_cost_ceiling_usd):
        return None
    if (price := PRICES.get(model)) is None:
        logger.warning(model, extra_fields={"event": "llm_unpriced_refused"})
        raise errors.UnpricedModelError(model)
    if not (worst := price.cost(est_in, cap)):
        return 0.0  # a free model never reads the ledger
    async with _lock:
        try:
            async with get_async_session() as session:
                spent = await deployment_cost_30d(session)  # live, not cached
        except Exception as e:
            raise errors.SpendPausedError(errors.ACCOUNTING_UNAVAILABLE) from e
        if spent + _inflight + worst > ceiling:
            raise errors.SpendPausedError(errors.MONTHLY_CEILING)
        _inflight += worst
    return worst


async def release(hold: float | None) -> None:
    global _inflight
    if hold:
        async with _lock:
            _inflight -= hold
