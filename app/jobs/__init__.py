"""Durable Postgres-backed background jobs.

- repository: enqueue/claim/succeed/fail/reap primitives over an AsyncSession.
- handlers: dispatch table mapping job kind -> workflow entrypoint.
- worker: single-consumer loop started from the app lifespan.

The worker is WOKEN by enqueue rather than discovering work by polling. The
notifier below is that wire: the running JobWorker registers itself on start()
and clears the registration on stop(), so `repository.enqueue` can nudge it
without importing the worker (which would be a cycle) and without the API
layer knowing a worker exists at all.

Why it matters beyond tidiness: the app is a long-lived container against Neon,
whose compute autosuspends after ~5 minutes of connection inactivity and bills
by the hour it stays awake. A worker that polls an empty queue on a timer keeps
that compute pinned awake 24/7 and burns the whole free-tier allowance doing
nothing. An idle Polyphony must issue NO query at all.
"""

from typing import Callable, Optional

_notifier: Optional[Callable[[], None]] = None


def set_notifier(fn: Optional[Callable[[], None]]) -> None:
    """Register (or clear, with None) the callback that wakes the job worker."""
    global _notifier
    _notifier = fn


def notify_enqueued() -> None:
    """Wake the worker, if one is running in this process.

    Deliberately forgiving: no worker (tests, CLI, JOB_WORKER_ENABLED=false) is
    the normal case, and a broken notifier must never fail the enqueueing
    request — the job is already durable in Postgres either way.
    """
    fn = _notifier
    if fn is None:
        return
    try:
        fn()
    except Exception:  # pragma: no cover - a wake is best-effort
        pass
