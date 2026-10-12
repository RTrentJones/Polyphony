"""Requests-per-day ledger for the provider's daily quota (SPEC-03).

The free tier's binding limit is usually the vendor's requests-per-DAY ceiling,
not tokens (docs/BRD.md §6.1), and it is shared by every user of the
deployment. The vendor enforces it with a 429 after the fact; this ledger
enforces it BEFORE the call, so a job pauses until the vendor's day rolls over
instead of burning retries against a wall, and so the UI can show how many
requests are left today.

One row per (provider, model, vendor-local day), incremented atomically and
conditionally: the increment only happens while the count is under the
ceiling, so concurrent callers can never overshoot it.

Fail-soft by design: if the ledger cannot be reached the call proceeds (the
vendor's own 429 is still classified as quota by the client). The ledger is an
early, cheaper signal — never a new way for generation to break.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.logging_config import setup_logging

from .errors import DailyRequestLimitError

logger = setup_logging("llm.ledger")


def vendor_day(tz: str, now: Optional[datetime] = None) -> date:
    zone = ZoneInfo(tz)
    return (now.astimezone(zone) if now else datetime.now(zone)).date()


def seconds_until_reset(tz: str, now: Optional[datetime] = None) -> float:
    zone = ZoneInfo(tz)
    local = now.astimezone(zone) if now else datetime.now(zone)
    tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time())
    return max(60.0, (tomorrow.replace(tzinfo=zone) - local).total_seconds())


_RESERVE = text("""
    INSERT INTO llm_request_counts (provider, model, day, count)
    VALUES (:provider, :model, :day, 1)
    ON CONFLICT (provider, model, day)
    DO UPDATE SET count = llm_request_counts.count + 1
    WHERE llm_request_counts.count < :ceiling
    RETURNING count
    """)


# After a ledger failure, skip it for this long instead of paying a failed
# connection attempt on every LLM call (the ledger is advisory; fail-soft).
_UNAVAILABLE_BACKOFF_SECONDS = 60.0
_unavailable_until = 0.0


async def reserve(provider_id: str, model: str, ceiling: int, tz: str) -> None:
    """Count one request against today's ceiling, or raise DailyRequestLimitError.

    Called once per attempt that will actually reach the provider."""
    import time

    from app.core.database import get_async_session

    global _unavailable_until
    if time.monotonic() < _unavailable_until:
        return
    try:
        async with get_async_session() as session:
            row = (
                await session.execute(
                    _RESERVE,
                    {
                        "provider": provider_id,
                        "model": model,
                        "day": vendor_day(tz),
                        "ceiling": ceiling,
                    },
                )
            ).first()
            await session.commit()
    except Exception as e:  # pragma: no cover - fail-soft path
        _unavailable_until = time.monotonic() + _UNAVAILABLE_BACKOFF_SECONDS
        logger.warning(
            f"RPD ledger unavailable, proceeding without it: {e}",
            extra_fields={"event": "rpd_ledger_unavailable"},
        )
        return
    if row is None:
        raise DailyRequestLimitError(
            f"{provider_id}/{model} reached its {ceiling} requests/day ceiling",
            reset_after=seconds_until_reset(tz),
        )


async def used_today(session, provider_id: str, model: str, tz: str) -> int:
    row = (
        await session.execute(
            text(
                "SELECT count FROM llm_request_counts "
                "WHERE provider = :p AND model = :m AND day = :d"
            ),
            {"p": provider_id, "m": model, "d": vendor_day(tz)},
        )
    ).first()
    return int(row[0]) if row else 0
