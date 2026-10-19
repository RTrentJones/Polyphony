"""Job lifecycle (SPEC-07): visible pauses, bounded waits, cancel, idempotency.

One test per acceptance criterion, plus the supporting behaviour (the jobs API,
dedupe on every enqueueing endpoint, the bounded test-dialogue call).
"""

import ast
import dataclasses
import pathlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.orm_models import (
    BookPlan,
    Chapter,
    ContinuityReport,
    ExtractionRun,
    Job,
    Scene,
    Source,
)
from app.jobs import repository as jobs_repo
from app.jobs.cancel import JobCancelled
from app.jobs.handlers import HANDLERS
from app.jobs.worker import JobWorker
from app.llm.errors import QuotaExhaustedError, SpendPausedError, UnpricedModelError

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _bind(monkeypatch, session):
    import app.jobs.handlers as handlers_mod
    import app.jobs.repository as repo_mod
    import app.jobs.worker as worker_mod

    for mod in (worker_mod, handlers_mod, repo_mod):
        monkeypatch.setattr(mod, "get_async_session", lambda: _Ctx(session))
    monkeypatch.setattr(settings, "LLM_TIER", "free")


def _worker():
    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="t"
    )


async def _due(session, job):
    await session.refresh(job)
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()


# --- domain fixtures per job kind ------------------------------------------------
# Each returns (payload, domain, row model or None, in-progress status, terminal).


async def _scene(session, user, book):
    ch = Chapter(book_id=book.id, title="C", position=0)
    session.add(ch)
    await session.flush()
    row = Scene(user_id=user.id, chapter_id=ch.id, status="processing", position=0)
    session.add(row)
    await session.flush()
    return {"scene_id": str(row.id)}, ("scene", row.id), row, "processing"


async def _plan(session, user, book):
    row = BookPlan(book_id=book.id, kind="outline", content=[], status="generating")
    session.add(row)
    await session.flush()
    return {"plan_id": str(row.id)}, ("book_plan", row.id), row, "generating"


async def _run(session, user, book):
    src = Source(user_id=user.id, book_id=book.id, title="S", content_text="x")
    session.add(src)
    await session.flush()
    row = ExtractionRun(
        book_id=book.id, source_id=src.id, user_id=user.id, status="pending"
    )
    session.add(row)
    await session.flush()
    return {"run_id": str(row.id)}, ("extraction_run", row.id), row, "pending"


async def _report(session, user, book):
    row = ContinuityReport(book_id=book.id, scope="book", status="processing")
    session.add(row)
    await session.flush()
    return {"report_id": str(row.id)}, ("continuity_report", row.id), row, "processing"


async def _none(session, user, book):
    return {}, None, None, None


DOMAIN = {
    "generate_prose_scene": _scene,
    "generate_ensemble_scene": _scene,
    "generate_outline": _plan,
    "extract_canon": _run,
    "continuity_check": _report,
    "index_characters_voice": _none,
    "revise_scene": _none,
}


def test_every_job_kind_has_every_lifecycle_hook():
    assert set(DOMAIN) == set(HANDLERS), "a new job kind needs a lifecycle test"
    for kind, handler in HANDLERS.items():
        for hook in ("on_dead", "on_pause", "on_resume", "on_cancel"):
            assert getattr(handler, hook) is not None, f"{kind} lacks {hook}"


# --- AC1: pause -> 'paused' -> resumed in progress -> terminal, per kind ----------


@pytest.mark.parametrize("kind", sorted(HANDLERS))
async def test_ac1_quota_pause_is_mirrored_then_resume_then_terminal(
    kind, async_session, test_user, test_book, monkeypatch
):
    _bind(monkeypatch, async_session)
    payload, domain, row, in_progress = await DOMAIN[kind](
        async_session, test_user, test_book
    )
    seen = {}

    async def run(p):
        if "first" not in seen:
            seen["first"] = True
            raise QuotaExhaustedError("429", reset_after=60)
        if row is not None:
            await async_session.refresh(row)
            seen["status_during_resume"] = row.status
            row.status = "completed" if isinstance(row, Scene) else "ready"
            await async_session.commit()

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod, "HANDLERS", {kind: dataclasses.replace(HANDLERS[kind], run=run)}
    )
    job = await jobs_repo.enqueue(
        async_session, kind=kind, payload=payload, user_id=test_user.id, domain=domain
    )
    await async_session.commit()

    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "queued" and job.paused_since is not None
    assert job.pause_count == 1
    if row is not None:
        await async_session.refresh(row)
        assert row.status == "paused"

    await _due(async_session, job)
    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "succeeded"
    if row is not None:
        assert seen["status_during_resume"] == in_progress  # on_resume ran first
        await async_session.refresh(row)
        assert row.status in ("completed", "ready")


async def test_job_api_reports_paused_with_resume_time(
    client, auth_headers, async_session, test_user, test_book, monkeypatch
):
    _bind(monkeypatch, async_session)
    payload, domain, row, _ = await _plan(async_session, test_user, test_book)
    job = await jobs_repo.enqueue(
        async_session,
        kind="generate_outline",
        payload=payload,
        user_id=test_user.id,
        domain=domain,
    )
    await async_session.commit()
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    job.status = "running"
    await jobs_repo.pause(async_session, job, available_at=resume_at, reason="quota")
    await async_session.commit()

    r = await client.get(f"/api/v1/jobs/{job.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paused"
    assert body["resume_at"] is not None
    assert body["domain"] == {"type": "book_plan", "id": str(row.id)}
    assert body["cancellable"] is True

    # And the plan list carries the same summary, so the badge needs no extra call.
    plans = (
        await client.get(f"/api/v1/books/{test_book.id}/plans", headers=auth_headers)
    ).json()["plans"]
    assert plans[0]["job"]["status"] == "paused"
    assert plans[0]["job"]["resume_at"] == body["resume_at"]


async def test_job_api_is_owner_scoped(client, async_session, test_book, test_user):
    from app.core.orm_models import User
    from app.core.security import create_access_token

    other = User(email="other@example.com", hashed_password="x", full_name="O")
    async_session.add(other)
    await async_session.commit()
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id
    )
    await async_session.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
    }
    assert (
        await client.get(f"/api/v1/jobs/{job.id}", headers=headers)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/jobs/{job.id}/cancel", headers=headers)
    ).status_code == 404


# --- AC2: bounded pause -------------------------------------------------------------


async def test_ac2_pause_beyond_bound_goes_dead_with_quota_message(
    async_session, test_user, test_book, monkeypatch
):
    _bind(monkeypatch, async_session)
    monkeypatch.setattr(settings, "JOB_MAX_PAUSE_HOURS", 48.0)
    payload, domain, plan, _ = await _plan(async_session, test_user, test_book)

    async def run(p):
        raise QuotaExhaustedError("429", reset_after=900)

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "HANDLERS",
        {
            "generate_outline": dataclasses.replace(
                HANDLERS["generate_outline"], run=run
            )
        },
    )
    job = await jobs_repo.enqueue(
        async_session,
        kind="generate_outline",
        payload=payload,
        user_id=test_user.id,
        domain=domain,
    )
    # Frozen clock: the pause streak began 49 hours ago.
    job.paused_since = datetime.now(timezone.utc) - timedelta(hours=49)
    plan.status = "paused"
    await async_session.commit()

    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "dead"
    assert job.error == "quota did not return within 48h"
    await async_session.refresh(plan)
    assert plan.status == "failed"
    assert plan.error == "quota did not return within 48h"


async def test_pause_within_bound_still_pauses(async_session, test_user, monkeypatch):
    _bind(monkeypatch, async_session)

    async def run(p):
        raise QuotaExhaustedError("429", reset_after=900)

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "HANDLERS",
        {"k": dataclasses.replace(HANDLERS["index_characters_voice"], run=run)},
    )
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id
    )
    job.paused_since = datetime.now(timezone.utc) - timedelta(hours=47)
    await async_session.commit()
    await _worker().run_once()
    await async_session.refresh(job)
    assert job.status == "queued" and job.pause_count == 1


# --- AC3: cancelling a queued outline keeps the previous plan -----------------------


async def test_ac3_cancel_queued_outline_keeps_previous_plan(
    client, auth_headers, async_session, test_book, monkeypatch
):
    _bind(monkeypatch, async_session)
    previous = [{"title": "Ch 1", "summary": "Old outline", "children": []}]
    plan = BookPlan(
        book_id=test_book.id, kind="outline", content=previous, status="ready"
    )
    async_session.add(plan)
    await async_session.commit()

    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/generate",
        json={"kind": "outline", "chapters_target": 6},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    await async_session.refresh(plan)
    assert plan.status == "generating"

    c = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert c.status_code == 200, c.text
    assert c.json()["status"] == "cancelled"

    await async_session.refresh(plan)
    assert plan.content == previous
    assert plan.status == "ready"
    assert plan.stage is None
    # Cancelled is terminal: cancelling again is a conflict, not a no-op.
    again = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert again.status_code == 409


async def test_cancel_running_job_stops_at_the_next_seam(
    client, auth_headers, async_session, test_user, test_book, monkeypatch
):
    """A running job is flagged; the workflow raises JobCancelled at its seam and
    the worker records it as cancelled and runs on_cancel."""
    _bind(monkeypatch, async_session)
    payload, domain, scene, _ = await _scene(async_session, test_user, test_book)
    job = await jobs_repo.enqueue(
        async_session,
        kind="generate_prose_scene",
        payload=payload,
        user_id=test_user.id,
        domain=domain,
    )
    await async_session.commit()

    async def run(p):
        # The author clicks Cancel while the job runs...
        r = await client.post(f"/api/v1/jobs/{job.id}/cancel", headers=auth_headers)
        assert r.json()["status"] == "running"
        assert r.json()["cancel_requested"] is True
        # ...and the workflow reaches its next seam.
        from app.jobs.context import current_job

        await current_job().save({"beats": []})
        raise AssertionError("save() should have raised JobCancelled")

    import app.jobs.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "HANDLERS",
        {
            "generate_prose_scene": dataclasses.replace(
                HANDLERS["generate_prose_scene"], run=run
            )
        },
    )
    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "cancelled"
    assert job.checkpoint is None
    await async_session.refresh(scene)
    assert scene.status == "cancelled"


async def test_raise_if_cancelled_reads_the_flag(async_session, test_user, monkeypatch):
    import app.core.database as db_mod
    from app.jobs.cancel import raise_if_cancelled

    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    job = await jobs_repo.enqueue(
        async_session, kind="k", payload={}, user_id=test_user.id
    )
    await async_session.commit()
    await raise_if_cancelled(job.id)  # not requested: no-op
    job.cancel_requested = True
    await async_session.commit()
    with pytest.raises(JobCancelled):
        await raise_if_cancelled(job.id)


# --- AC4: idempotent enqueues ---------------------------------------------------------


async def test_ac4_two_generate_plan_calls_make_one_job(
    client, auth_headers, async_session, test_book
):
    url = f"/api/v1/books/{test_book.id}/plans/generate"
    r1 = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    r2 = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r1.json()["plan_id"] == r2.json()["plan_id"]
    jobs = (
        (await async_session.execute(select(Job).where(Job.kind == "generate_outline")))
        .scalars()
        .all()
    )
    assert len(jobs) == 1


async def test_dedupe_releases_once_the_job_is_terminal(
    client, auth_headers, async_session, test_book
):
    url = f"/api/v1/books/{test_book.id}/plans/generate"
    r1 = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    job = await async_session.get(Job, __import__("uuid").UUID(r1.json()["job_id"]))
    job.status = "succeeded"
    await async_session.commit()
    r2 = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    assert r2.json()["job_id"] != r1.json()["job_id"]


async def test_scene_generation_dedupes_on_idempotency_key(
    client, auth_headers, async_session, test_book
):
    ch = (
        await client.post(
            f"/api/v1/books/{test_book.id}/chapters",
            json={"title": "One"},
            headers=auth_headers,
        )
    ).json()
    mina = (
        await client.post(
            "/api/v1/characters/",
            json={"name": "Mina", "book_id": str(test_book.id)},
            headers=auth_headers,
        )
    ).json()
    url = f"/api/v1/books/chapters/{ch['id']}/scenes/generate"
    body = {
        "character_ids": [mina["id"]],
        "scene_description": "A storm breaks over the moor.",
        "setting": "moor",
        "emotional_tone": "ominous",
    }
    key = {**auth_headers, "Idempotency-Key": "click-1"}
    ra = await client.post(url, json=body, headers=key)
    assert ra.status_code == 200, ra.text
    a = ra.json()
    b = (await client.post(url, json=body, headers=key)).json()
    assert a["scene_id"] == b["scene_id"] and a["job_id"] == b["job_id"]
    # Without a key two scenes in one chapter are legitimate.
    c = (await client.post(url, json=body, headers=auth_headers)).json()
    assert c["scene_id"] != a["scene_id"]
    scenes = (
        (await async_session.execute(select(Scene).where(Scene.chapter_id.isnot(None))))
        .scalars()
        .all()
    )
    assert len(scenes) == 2


async def test_reprocess_while_extracting_returns_the_same_run(
    client, auth_headers, async_session, test_book
):
    src = (
        await client.post(
            "/api/v1/sources/paste",
            json={"title": "T", "content_text": "Zara.", "book_id": str(test_book.id)},
            headers=auth_headers,
        )
    ).json()
    url = f"/api/v1/books/{test_book.id}/sources/{src['id']}/extract"
    r = await client.post(url, headers=auth_headers)
    assert r.status_code in (200, 202), r.text
    assert r.json()["run_id"] == src["extraction_run_id"]
    assert r.json()["job_id"] == src["job_id"]


async def test_continuity_check_dedupes(
    client, auth_headers, async_session, test_book, monkeypatch
):
    ch = (
        await client.post(
            f"/api/v1/books/{test_book.id}/chapters",
            json={"title": "One"},
            headers=auth_headers,
        )
    ).json()
    await client.post(
        f"/api/v1/books/chapters/{ch['id']}/scenes",
        json={"content": "Milo walked into the rain and did not look back."},
        headers=auth_headers,
    )
    url = f"/api/v1/books/{test_book.id}/continuity"
    a = await client.post(url, json={"chapter_id": ch["id"]}, headers=auth_headers)
    b = await client.post(url, json={"chapter_id": ch["id"]}, headers=auth_headers)
    assert a.status_code == b.status_code == 200, a.text
    assert a.json()["job_id"] == b.json()["job_id"]
    assert a.json()["report_id"] == b.json()["report_id"]


# --- AC5: no unbounded LLM call in a request handler ----------------------------------

# Entry points that spend model calls. A route may only reach them by enqueueing.
_LLM_ENTRYPOINTS = (
    "get_llm_client",
    "generate_outline(",
    "generate_staged_outline",
    "run_continuity_check",
    "extract_canon(",
    "run_prose_scene_workflow",
    "run_ensemble",
    "plan_scene_beats",
    "generate_dialogue(",
)


def test_ac5_no_route_awaits_the_llm_except_test_dialogue():
    api = pathlib.Path(__file__).resolve().parents[2] / "app" / "api"
    offenders = []
    for path in sorted(api.glob("*.py")):
        src = path.read_text()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(src, node) or ""
            for needle in _LLM_ENTRYPOINTS:
                if needle in body and not (
                    path.name == "characters.py" and node.name == "test_dialogue"
                ):
                    offenders.append(f"{path.name}:{node.name} -> {needle}")
    assert offenders == [], offenders


async def test_single_call_outline_is_a_job_not_a_request(
    client, auth_headers, async_session, test_book
):
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/generate",
        json={"kind": "beat_sheet"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "generating"
    job = (await async_session.execute(select(Job))).scalar_one()
    assert job.payload["mode"] == "single"
    assert job.payload["kind"] == "beat_sheet"


async def test_single_mode_handler_writes_the_plan(
    async_session, test_user, test_book, monkeypatch
):
    import app.planning.outline as outline_mod

    _bind(monkeypatch, async_session)

    async def fake_generate(**kw):
        assert kw["kind"] == "beat_sheet"
        return [{"title": "Opening image", "summary": "s", "children": []}]

    monkeypatch.setattr(outline_mod, "generate_outline", fake_generate)
    plan = BookPlan(book_id=test_book.id, kind="beat_sheet", content=[])
    plan.status = "generating"
    async_session.add(plan)
    await async_session.commit()
    await HANDLERS["generate_outline"].run(
        {
            "plan_id": str(plan.id),
            "book_id": str(test_book.id),
            "user_id": str(test_user.id),
            "mode": "single",
            "kind": "beat_sheet",
        }
    )
    await async_session.refresh(plan)
    assert plan.status == "ready"
    assert plan.content[0]["title"] == "Opening image"


async def test_test_dialogue_checks_budget(
    client, auth_headers, async_session, test_character, monkeypatch
):
    from fastapi import HTTPException

    import app.api.characters as chars

    async def over_budget(db, user_id):
        raise HTTPException(status_code=429, detail="Daily budget used")

    async def never(**kw):  # pragma: no cover - must not be reached
        raise AssertionError("LLM called despite an exhausted budget")

    monkeypatch.setattr(chars, "check_user_budget", over_budget)
    monkeypatch.setattr(chars, "generate_dialogue", never)
    r = await client.post(
        f"/api/v1/characters/{test_character.id}/test-dialogue",
        json={"prompt": "Say hello"},
        headers=auth_headers,
    )
    assert r.status_code == 429


@pytest.mark.parametrize(
    "error, status",
    [(None, 504), (SpendPausedError("x"), 429), (UnpricedModelError("m"), 503)],
)
async def test_test_dialogue_is_bounded(
    client, auth_headers, async_session, test_character, monkeypatch, error, status
):
    import asyncio

    import app.api.characters as chars

    async def slow(**kw):  # SPEC-81: a spend pause is a 429, an unpriced model a 503
        if error:
            raise error
        await asyncio.sleep(5)

    async def ok(db, user_id):
        return None

    monkeypatch.setattr(chars, "check_user_budget", ok)
    monkeypatch.setattr(chars, "generate_dialogue", slow)
    monkeypatch.setattr(chars, "TEST_DIALOGUE_BUDGET_SECONDS", 0.05)
    r = await client.post(
        f"/api/v1/characters/{test_character.id}/test-dialogue",
        json={"prompt": "Say hello"},
        headers=auth_headers,
    )
    assert r.status_code == status


def test_test_dialogue_bound_is_below_the_proxy_timeout():
    import app.api.characters as chars

    assert 0 < chars.TEST_DIALOGUE_BUDGET_SECONDS < 100  # Cloudflare origin timeout


async def test_scene_provenance_must_come_from_the_same_book(
    client, auth_headers, async_session, test_book
):
    """A scene in book A cannot cite a source from the author's book B (the
    book is the root); a source in book A is accepted."""
    other = (
        await client.post("/api/v1/books/", json={"title": "B"}, headers=auth_headers)
    ).json()

    async def paste(book_id):
        r = await client.post(
            "/api/v1/sources/paste",
            json={"title": "T", "content_text": "Mina.", "book_id": book_id},
            headers=auth_headers,
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]

    foreign, own = await paste(other["id"]), await paste(str(test_book.id))
    ch = (
        await client.post(
            f"/api/v1/books/{test_book.id}/chapters",
            json={"title": "One"},
            headers=auth_headers,
        )
    ).json()
    mina = (
        await client.post(
            "/api/v1/characters/",
            json={"name": "Mina", "book_id": str(test_book.id)},
            headers=auth_headers,
        )
    ).json()
    url = f"/api/v1/books/chapters/{ch['id']}/scenes/generate"
    body = {
        "character_ids": [mina["id"]],
        "scene_description": "A storm breaks over the moor.",
        "setting": "moor",
        "emotional_tone": "ominous",
    }
    r = await client.post(
        url, json={**body, "source_id": foreign}, headers=auth_headers
    )
    assert r.status_code == 404, r.text
    r = await client.post(url, json={**body, "source_id": own}, headers=auth_headers)
    assert r.status_code == 200, r.text


async def test_an_extraction_queued_before_an_edit_retires_unspent(
    client, auth_headers, async_session, test_book, monkeypatch
):
    """Editing a source queues a run for the new text; the run queued for the
    old text must not extract the new text too (double spend, wrong run)."""
    import app.parsing.canon_extractor as extractor
    from app.jobs.handlers import SUPERSEDED_NOTE

    _bind(monkeypatch, async_session)

    async def must_not_run(*a, **kw):
        raise AssertionError("a superseded run spent model calls")

    monkeypatch.setattr(extractor, "extract_canon", must_not_run)
    src = (
        await client.post(
            "/api/v1/sources/paste",
            json={"title": "T", "content_text": "Zara.", "book_id": str(test_book.id)},
            headers=auth_headers,
        )
    ).json()
    old_job = await async_session.get(Job, UUID(src["job_id"]))
    old_payload = dict(old_job.payload)
    r = await client.patch(
        f"/api/v1/sources/{src['id']}",
        json={"content_text": "Zara and Milo."},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("extraction_run_id") != src["extraction_run_id"]

    # The edit cancels the queued run at once ...
    old_run = await async_session.get(ExtractionRun, UUID(src["extraction_run_id"]))
    await async_session.refresh(old_run)
    await async_session.refresh(old_job)
    assert old_run.status == "cancelled"
    assert old_job.status == "cancelled"
    # ... and a stale job that runs anyway (claimed before the edit landed)
    # still retires without spending.
    old_run.status = "pending"
    await async_session.commit()
    await HANDLERS["extract_canon"].run(old_payload)
    await async_session.refresh(old_run)
    assert old_run.status == "cancelled"
    assert old_run.error == SUPERSEDED_NOTE


async def test_a_lost_outline_race_returns_the_winning_job(
    client, auth_headers, async_session, test_book, monkeypatch
):
    """Both requests miss find_active; the dedupe index hands the loser the
    winner's job, read before the rollback expires it (no 500)."""
    import app.api.plans as plans_mod

    test_book.synopsis = "A lich audits the afterlife."
    await async_session.commit()
    url = f"/api/v1/books/{test_book.id}/plans/generate"
    first = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    assert first.status_code == 200, first.text

    winner_id = UUID(first.json()["job_id"])

    async def miss(db, key):
        return None

    async def winner(db, **kw):
        # What Postgres's savepoint recovery hands the loser: the winner's job,
        # loaded in the loser's session (repository.enqueue).
        return await db.get(Job, winner_id)

    monkeypatch.setattr(plans_mod.jobs_repo, "find_active", miss)
    monkeypatch.setattr(plans_mod.jobs_repo, "enqueue", winner)
    again = await client.post(url, json={"kind": "outline"}, headers=auth_headers)
    assert again.status_code == 200, again.text
    assert again.json()["job_id"] == first.json()["job_id"]


async def _voice_enqueue(session, user_id, ids, key="voice:b:s"):
    job = await jobs_repo.enqueue_merging(
        session,
        kind="index_characters_voice",
        payload={"source_id": "s", "book_id": "b", "character_ids": ids},
        user_id=user_id,
        max_attempts=3,
        dedupe_key=key,
        merge=jobs_repo.merge_id_lists("character_ids"),
    )
    await session.commit()
    return job


async def test_voice_index_merges_into_a_queued_job(async_session, test_user):
    """SPEC-07/08: a second approval while the first voice job is still queued
    unions its ids into that job instead of enqueuing another (or dropping them)."""
    a = await _voice_enqueue(async_session, test_user.id, ["c1", "c2"])
    b = await _voice_enqueue(async_session, test_user.id, ["c2", "c3"])
    assert b.id == a.id
    jobs = (
        (
            await async_session.execute(
                select(Job).where(Job.kind == "index_characters_voice")
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    await async_session.refresh(jobs[0])
    assert jobs[0].payload["character_ids"] == ["c1", "c2", "c3"]
    assert jobs[0].payload["source_id"] == "s"


async def test_voice_index_merges_into_a_paused_job(async_session, test_user):
    # A paused job is 'queued' with paused_since: still unclaimed, so it merges.
    a = await _voice_enqueue(async_session, test_user.id, ["c1"])
    a.paused_since = datetime.now(timezone.utc)
    await async_session.commit()
    b = await _voice_enqueue(async_session, test_user.id, ["c9"])
    assert b.id == a.id
    await async_session.refresh(a)
    assert a.payload["character_ids"] == ["c1", "c9"]


async def test_voice_index_while_running_enqueues_a_new_job(async_session, test_user):
    """A running job already read its payload; merging into it would lose the
    new ids. It releases its key and keeps running; a fresh job takes the ids."""
    a = await _voice_enqueue(async_session, test_user.id, ["c1"])
    claimed = await jobs_repo.claim_one(async_session, worker_id="w")
    assert claimed.id == a.id
    await async_session.commit()

    b = await _voice_enqueue(async_session, test_user.id, ["c2"])
    assert b.id != a.id
    await async_session.refresh(a)
    assert a.status == "running" and a.dedupe_key is None
    assert a.payload["character_ids"] == ["c1"]
    assert b.status == "queued" and b.dedupe_key == "voice:b:s"
    assert b.payload["character_ids"] == ["c2"]

    # and a third approval merges into the NEW queued job
    c = await _voice_enqueue(async_session, test_user.id, ["c3"])
    assert c.id == b.id
    await async_session.refresh(b)
    assert b.payload["character_ids"] == ["c2", "c3"]


async def test_voice_index_keys_are_per_source(async_session, test_user):
    a = await _voice_enqueue(async_session, test_user.id, ["c1"], key="voice:b:s1")
    b = await _voice_enqueue(async_session, test_user.id, ["c1"], key="voice:b:s2")
    assert a.id != b.id


async def test_voice_index_after_terminal_enqueues_fresh(async_session, test_user):
    a = await _voice_enqueue(async_session, test_user.id, ["c1"])
    a.status = "succeeded"
    await async_session.commit()
    b = await _voice_enqueue(async_session, test_user.id, ["c2"])
    assert b.id != a.id
    assert b.payload["character_ids"] == ["c2"]


async def test_two_commits_from_one_source_share_one_voice_job(
    client, auth_headers, async_session, test_book, test_source
):
    """Two extraction runs of the same source committed back to back: the second
    commit's approved character joins the still-queued voice job."""
    from app.core.orm_models import ExtractionRun

    job_ids, created = [], []
    for name in ("Edric Thane", "Mina Hale"):
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add(run)
        await async_session.commit()
        r = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [{"name": name, "role": "lead"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        job_ids.append(r.json()["job_id"])
        created += r.json()["result"]["characters"]["created"]

    assert job_ids[0] == job_ids[1]
    job = (
        await async_session.execute(
            select(Job).where(Job.kind == "index_characters_voice")
        )
    ).scalar_one()
    await async_session.refresh(job)
    assert job.payload["character_ids"] == created
    assert job.dedupe_key == f"voice:{test_book.id}:{test_source.id}"
