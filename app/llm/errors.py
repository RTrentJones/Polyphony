"""LLM error types that the job layer classifies distinctly (docs/BRD.md R7.2)."""

from __future__ import annotations

from typing import Optional

PAUSE_REASON = "quota exhausted"  # a pause's reason: the job's error text
MONTHLY_CEILING, ACCOUNTING_UNAVAILABLE = "monthly_ceiling", "accounting_unavailable"
PAUSE_REASONS = (PAUSE_REASON, MONTHLY_CEILING, ACCOUNTING_UNAVAILABLE)


class QuotaExhaustedError(RuntimeError):
    """The provider's rate/quota is exhausted (429 / RESOURCE_EXHAUSTED) after the
    per-call retries gave up — as opposed to a transient connection/timeout error.

    On the FREE tier a job that surfaces this PAUSES: it re-queues with
    ``available_at = now + reset_after`` and the worker resumes it automatically
    when quota returns, with no lost work and no double-spend. On the PAID tier a
    429 is a real error and the job fails. `reset_after` carries the server's
    retry-after hint (seconds) when one was provided.
    """

    def __init__(
        self,
        message: str = "LLM quota exhausted",
        *,
        reset_after: Optional[float] = None,
        reason: str = PAUSE_REASON,
    ):
        super().__init__(message)
        self.reset_after, self.reason = reset_after, reason


class ProviderUnavailableError(QuotaExhaustedError):
    """The provider is failing persistently (the circuit breaker is open).

    A subclass of QuotaExhaustedError on purpose: to a job it means the same
    thing — the work is fine, the capacity is temporarily gone — so the job
    PAUSES until the breaker's recovery window instead of failing (SPEC-03).
    Unlike a quota 429 it pauses on every tier: an outage is never the
    author's error, and failing the job would lose their work.
    """


class UserBudgetExhaustedError(QuotaExhaustedError):
    """The author's rolling-24h token budget is spent (checked before every
    call, app/llm/client.py). Like an outage it pauses on every tier: the
    budget refills as the window rolls, and the job resumes where it stopped
    instead of overspending or losing its work."""


class DailyRequestLimitError(QuotaExhaustedError):
    """The provider's requests-per-day ceiling is reached (tracked locally in
    the request ledger, app/llm/ledger.py). Raised BEFORE calling the
    provider; `reset_after` is the time until the provider's day rolls over."""


class SpendPausedError(QuotaExhaustedError):  # not admitted under a ceiling
    def __init__(self, reason: str):
        super().__init__(f"spend paused: {reason}", reason=reason)


class UnpricedModelError(RuntimeError):  # under a ceiling: fails the job
    def __init__(self, model: str):
        super().__init__(f"'{model}' has no PRICES row; add one with source and date")
