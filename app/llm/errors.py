"""LLM error types that the job layer classifies distinctly (docs/BRD.md R7.2)."""

from __future__ import annotations

from typing import Optional


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
    ):
        super().__init__(message)
        self.reset_after = reset_after
