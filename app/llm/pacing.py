"""Free-tier request pacing for LLM calls.

A per-provider token bucket keeps us under the vendor's requests-per-minute
budget (Gemini's AI Studio free tier is ~10 RPM), and a small semaphore caps
in-flight calls. LLM-heavy background work additionally serializes behind the
single-consumer job worker (app/jobs/worker.py) so concurrent users queue
instead of dueling over the same RPM budget.
"""

import asyncio
import random
import time
from typing import Optional

from app.core.config import settings
from .providers import Provider


class RateLimiter:
    """Async token bucket: `rpm` requests per 60s, burst up to `rpm`."""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self._interval = 60.0 / self.rpm
        self._tokens = float(self.rpm)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    float(self.rpm),
                    self._tokens + (now - self._last_refill) / self._interval,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) * self._interval
            await asyncio.sleep(wait)


class ProviderPacer:
    """RPM bucket + concurrency cap for one provider."""

    def __init__(self, provider: Provider):
        rpm = settings.LLM_MAX_RPM or provider.max_rpm
        self.limiter = RateLimiter(rpm)
        self.semaphore = asyncio.Semaphore(max(1, settings.LLM_MAX_CONCURRENCY))

    async def __aenter__(self):
        await self.semaphore.acquire()
        try:
            await self.limiter.acquire()
        except BaseException:
            self.semaphore.release()
            raise
        return self

    async def __aexit__(self, *exc):
        self.semaphore.release()
        return False


_pacers: dict[str, ProviderPacer] = {}


def get_pacer(provider: Provider) -> ProviderPacer:
    pacer = _pacers.get(provider.id)
    if pacer is None:
        pacer = ProviderPacer(provider)
        _pacers[provider.id] = pacer
    return pacer


def reset_pacers() -> None:
    """Test hook: drop cached pacers (they bind the event loop at creation)."""
    _pacers.clear()


async def backoff_after_429(retry_after: Optional[float], attempt: int) -> None:
    """Sleep for the server-suggested interval, else jittered exponential."""
    if retry_after and retry_after > 0:
        delay = min(retry_after, 120.0)
    else:
        delay = min(2.0 ** (attempt + 1), 60.0)
    delay *= 0.8 + random.random() * 0.4  # nosec B311 - jitter, not crypto
    await asyncio.sleep(delay)
