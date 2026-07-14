"""Unit tests for the job worker loop (run_once driven directly, sqlite)."""

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
