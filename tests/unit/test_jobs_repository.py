"""Unit tests for the durable-jobs repository (sqlite).

Postgres-only semantics (FOR UPDATE SKIP LOCKED between two sessions) are
covered by the RUN_PG_TESTS integration suite; everything else about the
queue's state machine is exercised here.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.orm_models import Job
from app.jobs import repository as jobs_repo


def _now():
    return datetime.now(timezone.utc)


async def test_enqueue_defaults(async_session, test_user):
    job = await jobs_repo.enqueue(
        async_session,
        kind="generate_scene",
        payload={"scene_id": "abc"},
        user_id=test_user.id,
    )
    await async_session.commit()

    row = (await async_session.execute(select(Job))).scalar_one()
    assert row.id == job.id
    assert row.status == "queued"
    assert row.kind == "generate_scene"
    assert row.payload == {"scene_id": "abc"}
    assert row.attempts == 0
    assert row.max_attempts == 1
    assert row.locked_at is None
    assert row.available_at is not None


async def test_claim_marks_running_and_sets_lock_fields(async_session, test_user):
    await jobs_repo.enqueue(async_session, kind="k", payload={}, user_id=test_user.id)
    await async_session.commit()

    now = _now()
    job = await jobs_repo.claim_one(async_session, worker_id="w1", now=now)
    assert job is not None
    assert job.status == "running"
    assert job.attempts == 1
    assert job.locked_by == "w1"
    assert job.locked_at == now
    assert job.started_at == now


async def test_claim_skips_future_available_at(async_session, test_user):
    await jobs_repo.enqueue(
        async_session,
        kind="k",
        payload={},
        user_id=test_user.id,
        available_at=_now() + timedelta(minutes=5),
    )
    await async_session.commit()

    assert await jobs_repo.claim_one(async_session, worker_id="w1") is None


async def test_claim_is_fifo(async_session, test_user):
    t0 = _now()
    first = await jobs_repo.enqueue(
        async_session, kind="k", payload={"n": 1}, user_id=test_user.id
    )
    second = await jobs_repo.enqueue(
        async_session, kind="k", payload={"n": 2}, user_id=test_user.id
    )
    # sqlite CURRENT_TIMESTAMP has second precision; make order explicit.
    first.created_at = t0
    second.created_at = t0 + timedelta(seconds=1)
    await async_session.commit()

    claimed = await jobs_repo.claim_one(async_session, worker_id="w1")
    assert claimed.id == first.id


async def test_claim_returns_none_when_empty(async_session):
    assert await jobs_repo.claim_one(async_session, worker_id="w1") is None


async def test_mark_succeeded(async_session, test_user):
    await jobs_repo.enqueue(async_session, kind="k", payload={}, user_id=test_user.id)
    await async_session.commit()
    job = await jobs_repo.claim_one(async_session, worker_id="w1")

    await jobs_repo.mark_succeeded(async_session, job)
    assert job.status == "succeeded"
    assert job.finished_at is not None
    assert job.locked_at is None
    assert job.locked_by is None


async def test_mark_failed_requeues_with_backoff_then_dead(async_session, test_user):
    await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=2
    )
    await async_session.commit()

    job = await jobs_repo.claim_one(async_session, worker_id="w1")
    before = _now()
    went_dead = await jobs_repo.mark_failed(async_session, job, "boom")
    assert went_dead is False
    assert job.status == "queued"
    assert job.error == "boom"
    assert job.locked_at is None
    # attempt 1 -> backoff BASE * 2**0
    assert job.available_at >= before + timedelta(
        seconds=jobs_repo.BACKOFF_BASE_SECONDS - 1
    )

    # Second (final) attempt goes dead.
    job2 = await jobs_repo.claim_one(
        async_session, worker_id="w1", now=job.available_at + timedelta(seconds=1)
    )
    assert job2.id == job.id
    assert job2.attempts == 2
    went_dead = await jobs_repo.mark_failed(async_session, job2, "boom again")
    assert went_dead is True
    assert job2.status == "dead"
    assert job2.finished_at is not None


async def test_backoff_is_capped(async_session, test_user):
    await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=99
    )
    await async_session.commit()
    job = await jobs_repo.claim_one(async_session, worker_id="w1")
    job.attempts = 50  # absurd attempt count -> delay must be capped
    before = _now()
    await jobs_repo.mark_failed(async_session, job, "boom")
    assert job.available_at <= before + timedelta(
        seconds=jobs_repo.BACKOFF_CAP_SECONDS + 1
    )


async def test_reap_stale_requeues_old_running_leaves_fresh(async_session, test_user):
    await jobs_repo.enqueue(
        async_session,
        kind="k",
        payload={"n": "stale"},
        user_id=test_user.id,
        max_attempts=2,
    )
    await jobs_repo.enqueue(
        async_session,
        kind="k",
        payload={"n": "fresh"},
        user_id=test_user.id,
        max_attempts=2,
    )
    await async_session.commit()

    stale = await jobs_repo.claim_one(async_session, worker_id="w1")
    fresh = await jobs_repo.claim_one(async_session, worker_id="w1")
    assert stale is not None and fresh is not None
    stale.locked_at = _now() - timedelta(hours=2)
    await async_session.flush()

    results = await jobs_repo.reap_stale(
        async_session, stale_after=timedelta(minutes=30)
    )
    assert [(j.id, dead) for j, dead in results] == [(stale.id, False)]
    assert stale.status == "queued"
    assert "stale" in stale.error
    assert fresh.status == "running"


async def test_reap_stale_kills_exhausted_job(async_session, test_user):
    await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id, max_attempts=1
    )
    await async_session.commit()
    job = await jobs_repo.claim_one(async_session, worker_id="w1")
    job.locked_at = _now() - timedelta(hours=2)
    await async_session.flush()

    results = await jobs_repo.reap_stale(
        async_session, stale_after=timedelta(minutes=30)
    )
    assert [(j.id, dead) for j, dead in results] == [(job.id, True)]
    assert job.status == "dead"
