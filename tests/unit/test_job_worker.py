"""Unit tests for the job worker loop (run_once driven directly, sqlite)."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.orm_models import Scene
from app.jobs import repository as jobs_repo
from app.jobs.handlers import Handler, _dead_scene
from app.jobs.worker import JobWorker


class _Ctx:
    """Async context manager yielding the shared test session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _bind_sessions(monkeypatch, async_session):
    import app.jobs.handlers as handlers_mod
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))


def _worker():
    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="test-w"
    )


async def test_run_once_empty_queue_returns_false(async_session, monkeypatch):
    _bind_sessions(monkeypatch, async_session)
    assert await _worker().run_once() is False


async def test_run_once_success_path(async_session, test_user, monkeypatch):
    _bind_sessions(monkeypatch, async_session)
    ran_payloads = []

    async def fake_run(payload):
        ran_payloads.append(payload)

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "HANDLERS", {"k": Handler(run=fake_run)})

    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={"x": 1}, user_id=test_user.id
    )
    await async_session.commit()

    assert await _worker().run_once() is True
    assert ran_payloads == [{"x": 1}]
    await async_session.refresh(job)
    assert job.status == "succeeded"


async def test_run_once_failure_requeues_then_dead_runs_on_dead(
    async_session, test_user, monkeypatch
):
    """A failing handler retries with backoff, then goes dead and the
    on_dead hook flips the related scene out of 'processing'."""
    _bind_sessions(monkeypatch, async_session)

    scene = Scene(user_id=test_user.id, title="S", status="processing", position=0)
    async_session.add(scene)
    await async_session.commit()

    async def failing_run(payload):
        raise RuntimeError("provider exploded")

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "HANDLERS",
        {"k": Handler(run=failing_run, on_dead=_dead_scene)},
    )

    job = await jobs_repo.enqueue(
        async_session,
        kind="k",
        payload={"scene_id": str(scene.id)},
        user_id=test_user.id,
        max_attempts=2,
    )
    await async_session.commit()

    worker = _worker()
    assert await worker.run_once() is True
    await async_session.refresh(job)
    assert job.status == "queued"  # first failure -> retry with backoff
    assert job.attempts == 1
    assert "provider exploded" in job.error
    await async_session.refresh(scene)
    assert scene.status == "processing"  # not dead yet

    # Make the retry due now, then fail it for good.
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await async_session.commit()

    assert await worker.run_once() is True
    await async_session.refresh(job)
    assert job.status == "dead"
    assert job.attempts == 2
    await async_session.refresh(scene)
    assert scene.status == "failed"  # on_dead ran


async def test_quota_pause_marks_scene_paused_then_resume_completes(
    async_session, test_user, monkeypatch
):
    """A QuotaExhaustedError pauses the job AND flips the scene to 'paused' (not a
    stuck 'processing'); a successful resume flips it back to 'completed'."""
    _bind_sessions(monkeypatch, async_session)

    from app.core.orm_models import Scene as SceneORM
    from app.jobs.handlers import _pause_scene
    from app.llm.errors import QuotaExhaustedError

    scene = Scene(user_id=test_user.id, title="S", status="processing", position=0)
    async_session.add(scene)
    await async_session.commit()

    calls = {"n": 0}

    async def run(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise QuotaExhaustedError("quota gone", reset_after=1)
        # resume: the workflow would generate and complete the scene
        s = await async_session.get(SceneORM, scene.id)
        s.status = "completed"
        await async_session.commit()

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "HANDLERS",
        {"k": Handler(run=run, on_dead=_dead_scene, on_pause=_pause_scene)},
    )

    job = await jobs_repo.enqueue(
        async_session,
        kind="k",
        payload={"scene_id": str(scene.id)},
        user_id=test_user.id,
        max_attempts=1,
    )
    await async_session.commit()

    worker = _worker()
    # 1) quota -> PAUSED (job re-queued, not dead; scene reflects the pause)
    assert await worker.run_once() is True
    await async_session.refresh(job)
    assert job.status == "queued"
    await async_session.refresh(scene)
    assert scene.status == "paused"

    # 2) resume when due -> succeeds -> scene completed
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await async_session.commit()
    assert await worker.run_once() is True
    await async_session.refresh(scene)
    assert scene.status == "completed"


async def test_run_once_unknown_kind_goes_dead_immediately(
    async_session, test_user, monkeypatch
):
    _bind_sessions(monkeypatch, async_session)
    job = await jobs_repo.enqueue(
        async_session,
        kind="nope",
        payload={},
        user_id=test_user.id,
        max_attempts=5,
    )
    await async_session.commit()

    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "dead"
    assert "unknown job kind" in job.error


async def test_boot_reap_requeues_orphaned_running_job(
    async_session, test_user, monkeypatch
):
    """start()'s zero-tolerance reap: any 'running' job at boot is orphaned."""
    _bind_sessions(monkeypatch, async_session)
    await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=2
    )
    await async_session.commit()
    job = await jobs_repo.claim_one(async_session, worker_id="dead-worker")
    await async_session.commit()
    assert job.status == "running"

    worker = _worker()
    await worker._reap(stale_after=timedelta(0))
    await async_session.refresh(job)
    assert job.status == "queued"


# --- event-driven idle: the worker must not poll an empty queue --------------
# Neon bills compute by wall-clock time awake, so "issues no query while idle"
# is a correctness property here, not a performance nicety.


async def test_enqueue_notifies_the_worker_on_commit_not_on_flush(
    async_session, test_user, monkeypatch
):
    """The wake must wait for the COMMIT, not fire at flush.

    Firing at flush races the caller's commit: the worker looks, the row is not
    visible, and with nothing else queued it sleeps indefinitely on a job that
    lands a moment later. Waiting for after_commit means the row is claimable
    whenever the worker is woken.
    """
    from app import jobs as jobs_pkg

    worker = _worker()
    jobs_pkg.set_notifier(worker.notify)
    try:
        assert worker._wake.is_set() is False
        await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id
        )
        assert worker._wake.is_set() is False, "woke before the commit"

        await async_session.commit()
        assert worker._wake.is_set() is True
    finally:
        jobs_pkg.set_notifier(None)


async def test_a_slow_commit_still_wakes_the_worker(
    async_session, test_user, monkeypatch
):
    """The case a bounded settle window could not cover.

    A caller that holds its transaction open longer than SETTLE_POLLS *
    poll_interval used to exhaust every recheck before the row existed, leaving
    the job stranded. The wake now rides the commit, however late it is.
    """
    import app.jobs.worker as worker_mod
    from app import jobs as jobs_pkg

    worker = _worker()
    jobs_pkg.set_notifier(worker.notify)
    try:
        await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id
        )
        # Outlast the whole settle window before committing.
        await asyncio.sleep(worker.poll_interval * (worker_mod.SETTLE_POLLS + 1))
        assert worker._wake.is_set() is False

        await async_session.commit()
        assert worker._wake.is_set() is True
    finally:
        jobs_pkg.set_notifier(None)


async def test_rolled_back_enqueue_does_not_wake_on_that_transaction(
    async_session, test_user
):
    """No commit, no wake — the job never existed."""
    from app import jobs as jobs_pkg

    worker = _worker()
    jobs_pkg.set_notifier(worker.notify)
    try:
        await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id
        )
        await async_session.rollback()
        assert worker._wake.is_set() is False
    finally:
        jobs_pkg.set_notifier(None)


async def test_notify_with_no_worker_is_a_noop(async_session, test_user):
    """The CLI and the tests enqueue with no worker running; that must be fine."""
    from app import jobs as jobs_pkg

    jobs_pkg.set_notifier(None)
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id
    )
    await async_session.commit()
    assert job.status == "queued"


async def test_notify_failure_never_breaks_enqueue(async_session, test_user):
    from app import jobs as jobs_pkg

    def boom():
        raise RuntimeError("worker exploded")

    jobs_pkg.set_notifier(boom)
    try:
        job = await jobs_repo.enqueue(
            async_session, kind="k", payload={}, user_id=test_user.id
        )
        await async_session.commit()
        assert job.status == "queued"  # durable regardless of the wake
    finally:
        jobs_pkg.set_notifier(None)


async def test_idle_state_empty_queue(async_session, monkeypatch):
    next_at, has_running = await jobs_repo.idle_state(async_session)
    assert next_at is None
    assert has_running is False


async def test_next_deadline_is_none_when_nothing_to_do(async_session, monkeypatch):
    """The load-bearing case: no queued work, no running work -> sleep forever.

    A number here instead of None would be a timer, and a timer against Neon is
    a 24/7 compute bill.
    """
    _bind_sessions(monkeypatch, async_session)
    assert await _worker()._next_deadline() is None


async def test_next_deadline_waits_for_a_paused_job(
    async_session, test_user, monkeypatch
):
    """A quota pause schedules one wake at the resume time, not a poll."""
    _bind_sessions(monkeypatch, async_session)
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id
    )
    job.available_at = datetime.now(timezone.utc) + timedelta(seconds=300)
    await async_session.commit()

    delay = await _worker()._next_deadline()
    assert delay is not None
    assert 250 < delay <= 300  # sleeps until it is due, not on a tick


async def test_next_deadline_keeps_the_reaper_alive_while_a_job_runs(
    async_session, test_user, monkeypatch
):
    _bind_sessions(monkeypatch, async_session)
    await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=2
    )
    await async_session.commit()
    await jobs_repo.claim_one(async_session, worker_id="other-worker")
    await async_session.commit()

    from app.jobs.worker import REAP_INTERVAL_SECONDS

    assert await _worker()._next_deadline() == float(REAP_INTERVAL_SECONDS)


async def test_idle_poll_ceiling_is_opt_in(async_session, monkeypatch):
    """JOB_IDLE_POLL_SECONDS caps the sleep for multi-writer deployments."""
    _bind_sessions(monkeypatch, async_session)
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod.settings, "JOB_IDLE_POLL_SECONDS", 120.0)
    assert await _worker()._next_deadline() == 120.0


async def test_loop_sleeps_instead_of_polling_an_empty_queue(
    async_session, monkeypatch
):
    """End-to-end: run the real loop and count the claims it makes.

    With the old 2s poll this made a claim every tick forever. Now it claims
    once, finds nothing, exhausts the settle window and parks.
    """
    _bind_sessions(monkeypatch, async_session)
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "HANDLERS", {})
    claims = {"n": 0}
    real_claim = jobs_repo.claim_one

    async def counting_claim(session, **kw):
        claims["n"] += 1
        return await real_claim(session, **kw)

    monkeypatch.setattr(worker_mod.jobs_repo, "claim_one", counting_claim)

    worker = JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="test-w"
    )
    await worker.start()
    await asyncio.sleep(0.3)  # >> 30 poll intervals under the old loop
    settled = claims["n"]
    await asyncio.sleep(0.3)
    assert claims["n"] == settled, "worker is still polling an idle queue"
    assert settled <= 1 + worker_mod.SETTLE_POLLS
    await worker.stop()


async def test_stop_breaks_an_indefinite_idle_wait(async_session, monkeypatch):
    """Nothing queued means an unbounded sleep; shutdown must still be prompt."""
    _bind_sessions(monkeypatch, async_session)
    import app.jobs.worker as worker_mod

    monkeypatch.setattr(worker_mod, "HANDLERS", {})
    worker = JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="test-w"
    )
    await worker.start()
    await asyncio.sleep(0.2)
    await asyncio.wait_for(worker.stop(), timeout=2)
    from app import jobs as jobs_pkg

    assert jobs_pkg._notifier is None  # deregistered on stop
