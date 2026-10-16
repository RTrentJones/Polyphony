"""SPEC-10 / SPEC-11 follow-ups.

* Grounding records keep a bounded excerpt of each sample, so a sample deleted
  since generation still shows its wording (SPEC-11, R5.3).
* Every character in a beat has a grounding entry, with a reason when no sample
  grounded it (none indexed; ensemble context-only).
* A chapter's versions carry its promoted cast and POV, and restore them
  (SPEC-10 x SPEC-13).
* "Generate all planned scenes": one scene per planned beat, as a chain of jobs
  so each scene starts from the one before it.
"""

import types
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.characters import context as ctx_mod
from app.core.config import settings
from app.core.orm_models import Chapter, Job, Scene
from app.jobs import scene_batch
from tests.unit.test_outline_handoff import _bind, _cast, _structured_plan, _worker
from tests.unit.test_voice_workbench import _LLM, _PLAN, _Ctx, duo  # noqa: F401

pytestmark = pytest.mark.unit


# --- grounding excerpts and reasons (SPEC-11) ----------------------------------


def test_grounding_entries_keep_a_bounded_excerpt():
    character = types.SimpleNamespace(id=uuid.uuid4())
    long_text = "word " * 200
    entries = ctx_mod.grounding_entries(
        character,
        [
            {"id": "a", "score": 0.7, "text": "Short and whole."},
            {"id": "b", "score": 0.4, "text": long_text},
        ],
    )
    assert entries[0]["excerpt"] == "Short and whole."
    cut = entries[1]["excerpt"]
    assert cut.endswith("…")
    assert len(cut) <= ctx_mod.GROUNDING_EXCERPT_CHARS + 1
    assert entries[1]["chunk_id"] == "b" and entries[1]["score"] == 0.4


def test_a_character_without_samples_gets_a_reason_entry():
    character = types.SimpleNamespace(id=uuid.uuid4())
    assert ctx_mod.grounding_entries(character, []) == [
        {
            "character_id": str(character.id),
            "chunk_id": None,
            "score": None,
            "reason": "no indexed voice samples",
        }
    ]


class _HalfIndexedStore:
    """Milo has one sample; everyone else has none."""

    def __init__(self, milo_id):
        self.milo_id = str(milo_id)

    async def retrieve_similar(self, *, character_id, **kw):
        if character_id != self.milo_id:
            return []
        return [{"id": "m-1", "text": "I keep my own counsel.", "score": 0.6}]


async def test_prose_lists_unindexed_characters_with_a_reason(
    async_session, duo, monkeypatch  # noqa: F811
):
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod
    from app.orchestration.prose import run_prose_scene_workflow

    llm = _LLM({"plan_beats": _PLAN, "beat_prose": "Prose. " * 20})
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: llm)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: llm)
    monkeypatch.setattr(
        ctx_mod, "get_chunk_store", lambda: _HalfIndexedStore(duo.milo.id)
    )

    scene = Scene(user_id=duo.book.user_id, book_id=duo.book.id, status="processing")
    async_session.add(scene)
    await async_session.commit()
    result = await run_prose_scene_workflow(
        scene.id,
        {
            "character_ids": [str(duo.milo.id), str(duo.zara.id)],
            "characters": [],
            "scene_description": "They meet.",
            "target_word_count": 200,
        },
        duo.book.user_id,
        chapter_summary="",
        story_position=None,
        book_id=duo.book.id,
    )
    assert result["status"] == "completed", result
    beat0 = {e["character_id"]: e for e in scene.evaluation_scores["grounding"]["0"]}
    assert beat0[str(duo.milo.id)]["excerpt"] == "I keep my own counsel."
    assert beat0[str(duo.zara.id)] == {
        "character_id": str(duo.zara.id),
        "chunk_id": None,
        "score": None,
        "reason": "no indexed voice samples",
    }


async def test_ensemble_records_context_only_characters(
    async_session, duo, monkeypatch  # noqa: F811
):
    import app.orchestration.ensemble as ens

    llm = _LLM(
        {
            "ensemble:proposal": '{"intent":"i","actions":["nods"],"lines":["Hi"]}',
            "ensemble:narration": "Milo nods and Zara watches him. " * 3,
            "ensemble:review": '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":["Milo","Zara"],"objections":[]}',
        }
    )
    monkeypatch.setattr(ens, "get_llm_client", lambda: llm)
    monkeypatch.setattr(
        ctx_mod, "get_chunk_store", lambda: _HalfIndexedStore(duo.milo.id)
    )
    # One proposal agent: Zara is context-only.
    monkeypatch.setattr(
        ens,
        "get_tier",
        lambda: types.SimpleNamespace(max_agents=1, max_rounds=1),
    )

    async def _load(names, **kw):
        rows = {"Milo": duo.milo, "Zara": duo.zara}
        return {n: rows[n] for n in names if n in rows}

    monkeypatch.setattr(ens, "load_characters_for_book", _load)
    result = await ens.run_ensemble(
        scene_request={"characters": ["Milo", "Zara"]},
        beat_description="They meet.",
        user_id=duo.book.user_id,
        book_id=duo.book.id,
        canon_context="",
        scope=ens.SceneScope((duo.milo, duo.zara), (), (), frozenset(), "", ""),
    )
    by_char = {e["character_id"]: e for e in result["grounding"]}
    assert by_char[str(duo.milo.id)]["chunk_id"] == "m-1"
    assert by_char[str(duo.zara.id)]["reason"] == "context only"
    assert by_char[str(duo.zara.id)]["chunk_id"] is None


async def test_grounding_endpoint_returns_stored_excerpt_and_reasons(
    client, auth_headers, async_session, duo  # noqa: F811
):
    milo, zara = str(duo.milo.id), str(duo.zara.id)
    scene = Scene(
        user_id=duo.book.user_id,
        book_id=duo.book.id,
        status="completed",
        evaluation_scores={
            "mode": "ensemble",
            "grounding": {
                "0": [
                    {
                        "character_id": milo,
                        "chunk_id": "gone-1",
                        "score": 0.5,
                        "excerpt": "What Milo said then.",
                    },
                    {
                        "character_id": zara,
                        "chunk_id": None,
                        "score": None,
                        "reason": "context only",
                    },
                ]
            },
        },
    )
    async_session.add(scene)
    await async_session.commit()

    fake = AsyncMock()
    fake.get_chunks.return_value = {}
    with patch("app.api.grounding.get_chunk_store", return_value=fake):
        r = await client.get(
            f"/api/v1/books/scenes/{scene.id}/grounding", headers=auth_headers
        )
    assert r.status_code == 200, r.text
    (beat,) = r.json()["beats"]
    by_char = {c["character_id"]: c for c in beat["characters"]}
    (gone,) = by_char[milo]["samples"]
    assert gone["deleted"] is True and gone["text"] is None
    assert gone["excerpt"] == "What Milo said then."
    assert by_char[milo]["reason"] is None
    assert by_char[zara]["name"] == "Zara"
    assert by_char[zara]["samples"] == []
    assert by_char[zara]["reason"] == "context only"
    # Only real chunk ids are resolved.
    assert fake.get_chunks.call_args.args[0] == ["gone-1"]


# --- chapter versions carry the promoted cast (SPEC-10 x SPEC-13) ----------------


async def _promoted_chapter(client, auth_headers, async_session, test_book):
    ids = await _cast(async_session, test_book)
    nodes = await _structured_plan(client, auth_headers, test_book, ids)
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_id": nodes[0]["node_id"]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return ids, nodes, r.json()["chapter_id"]


async def _versions(client, auth_headers, book_id, chapter_id):
    r = await client.get(
        f"/api/v1/books/{book_id}/versions/chapter/{chapter_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["versions"]


async def test_chapter_versions_snapshot_and_restore_the_promoted_cast(
    client, auth_headers, async_session, test_book
):
    book_id = test_book.id
    ids, nodes, chapter_id = await _promoted_chapter(
        client, auth_headers, async_session, test_book
    )
    v1 = (
        await client.get(
            f"/api/v1/books/{book_id}/versions/chapter/{chapter_id}/1",
            headers=auth_headers,
        )
    ).json()["content"]
    assert v1["pov_character_id"] == ids["Milo Voss"]
    assert v1["character_ids"] == [ids["Milo Voss"], ids["Zara Okafor"]]
    assert v1["plan_node_id"] == nodes[0]["node_id"]

    # The cast changes (as a later edit would), then the chapter is edited.
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    chapter.character_ids = [ids["Edric Thane"]]
    chapter.pov_character_id = uuid.UUID(ids["Edric Thane"])
    await async_session.commit()
    r = await client.patch(
        f"/api/v1/books/chapters/{chapter_id}",
        json={"title": "Renamed"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/v1/books/{book_id}/versions/chapter/{chapter_id}/restore/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert chapter.title == "Onboarding"
    assert str(chapter.pov_character_id) == ids["Milo Voss"]
    assert chapter.character_ids == [ids["Milo Voss"], ids["Zara Okafor"]]
    newest = (await _versions(client, auth_headers, book_id, chapter_id))[0]
    assert newest["reason"] == "restored_from:1"


async def test_restoring_a_chapter_drops_characters_trashed_since(
    client, auth_headers, async_session, test_book
):
    book_id = test_book.id
    ids, _, chapter_id = await _promoted_chapter(
        client, auth_headers, async_session, test_book
    )
    r = await client.delete(
        f"/api/v1/characters/{ids['Milo Voss']}", headers=auth_headers
    )
    assert r.status_code in (200, 204), r.text
    r = await client.post(
        f"/api/v1/books/{book_id}/versions/chapter/{chapter_id}/restore/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert chapter.pov_character_id is None  # Milo is in the Trash
    assert chapter.character_ids == [ids["Zara Okafor"]]


async def test_recreating_a_purged_chapter_keeps_its_node_only_if_free(
    client, auth_headers, async_session, test_book
):
    book_id = test_book.id
    ids, nodes, chapter_id = await _promoted_chapter(
        client, auth_headers, async_session, test_book
    )
    base = f"/api/v1/books/{book_id}"
    assert (
        await client.delete(
            f"/api/v1/books/chapters/{chapter_id}", headers=auth_headers
        )
    ).status_code in (200, 204)
    assert (
        await client.delete(f"{base}/trash/chapter/{chapter_id}", headers=auth_headers)
    ).status_code == 204

    r = await client.post(
        f"{base}/versions/chapter/{chapter_id}/restore/1", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert str(chapter.plan_node_id) == nodes[0]["node_id"]
    assert chapter.character_ids == [ids["Milo Voss"], ids["Zara Okafor"]]
    # Promote finds the recreated chapter: still one chapter per node.
    again = await client.post(
        f"{base}/plans/promote",
        json={"node_id": nodes[0]["node_id"]},
        headers=auth_headers,
    )
    assert again.json() == {**again.json(), "created": False, "chapter_id": chapter_id}

    # Purge it again, promote the node anew, then recreate: it comes back unlinked.
    await client.delete(f"/api/v1/books/chapters/{chapter_id}", headers=auth_headers)
    await client.delete(f"{base}/trash/chapter/{chapter_id}", headers=auth_headers)
    fresh = await client.post(
        f"{base}/plans/promote",
        json={"node_id": nodes[0]["node_id"]},
        headers=auth_headers,
    )
    assert fresh.json()["created"] is True
    r = await client.post(
        f"{base}/versions/chapter/{chapter_id}/restore/1", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert chapter.plan_node_id is None


# --- generate all planned scenes (SPEC-10 follow-up) -----------------------------


async def _two_beat_chapter(client, auth_headers, async_session, test_book):
    ids = await _cast(async_session, test_book)
    outline = [
        {
            "title": "Onboarding",
            "summary": "Milo arrives",
            "pov": "Milo Voss",
            "character_ids": [ids["Milo Voss"], ids["Zara Okafor"]],
            "children": [
                {"title": "Badge photo", "summary": "Milo learns he is dead"},
                {"title": "Lunch", "summary": "Zara explains the afterlife"},
            ],
        }
    ]
    r = await client.put(
        f"/api/v1/books/{test_book.id}/plans",
        json={"kind": "outline", "content": outline},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_index": 0},
        headers=auth_headers,
    )
    return ids, r.json()["chapter_id"]


async def test_generate_planned_enqueues_the_first_scene_of_a_chain(
    client, auth_headers, async_session, test_book
):
    ids, chapter_id = await _two_beat_chapter(
        client, auth_headers, async_session, test_book
    )
    url = f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned"
    r = await client.post(url, json={"setting": "the office"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["queued"] == 2 and body["skipped"] == 0
    assert body["estimate"]["calls"] > 0

    jobs = (await async_session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1  # a chain: only the first scene is enqueued now
    job = jobs[0]
    assert job.kind == "generate_prose_scene"
    assert job.dedupe_key.endswith(":0")
    request = job.payload["request"]
    assert request["scene_description"] == "Badge photo: Milo learns he is dead"
    assert request["character_ids"] == [ids["Milo Voss"], ids["Zara Okafor"]]
    assert request["pov_character"] == "Milo Voss"
    assert request["setting"] == "the office"
    assert request["beat_title"] == "Badge photo"
    (nxt,) = job.payload["batch"]["remaining"]
    assert nxt["scene_description"] == "Lunch: Zara explains the afterlife"
    assert nxt["beat_title"] == "Lunch"

    # A repeat while the batch is in flight returns it, spending nothing.
    again = await client.post(url, json={}, headers=auth_headers)
    assert again.status_code == 200, again.text
    assert again.json()["already_running"] is True
    assert again.json()["job_id"] == str(job.id)
    assert len((await async_session.execute(select(Job))).scalars().all()) == 1


async def test_generate_planned_retries_a_position_clash_raised_at_flush(
    client, auth_headers, async_session, test_book, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    _, chapter_id = await _two_beat_chapter(
        client, auth_headers, async_session, test_book
    )
    real = scene_batch.add_scene_job
    calls = {"n": 0}

    async def clash_once(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # What a concurrent append taking the same position raises: the
            # helper flushes the scene before the endpoint commits.
            raise IntegrityError("INSERT INTO scenes", {}, Exception("position"))
        return await real(session, **kwargs)

    monkeypatch.setattr(scene_batch, "add_scene_job", clash_once)
    r = await client.post(
        f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert calls["n"] == 2
    assert len((await async_session.execute(select(Job))).scalars().all()) == 1


async def test_an_in_flight_batch_reports_the_chapter_total(
    client, auth_headers, async_session, test_book
):
    """`total` is the chapter's planned-beat count even when some beats were
    written before the batch started (they are `skipped`)."""
    _, chapter_id = await _two_beat_chapter(
        client, auth_headers, async_session, test_book
    )
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    async_session.add(
        Scene(
            user_id=test_book.user_id,
            book_id=test_book.id,
            chapter_id=chapter.id,
            position=0,
            scene_description="Badge photo: Milo learns he is dead",
            scene_request={"planned_beat_id": chapter.planned_beats[0]["node_id"]},
            status="completed",
        )
    )
    await async_session.commit()
    url = f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned"
    first = (await client.post(url, json={}, headers=auth_headers)).json()
    assert (first["total"], first["queued"], first["skipped"]) == (2, 1, 1)
    again = (await client.post(url, json={}, headers=auth_headers)).json()
    assert again["already_running"] is True
    assert (again["total"], again["queued"], again["skipped"]) == (2, 1, 1)


async def test_generate_planned_needs_planned_beats(
    client, auth_headers, async_session, test_book
):
    r = await client.post(
        f"/api/v1/books/{test_book.id}/chapters",
        json={"title": "Loose"},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text
    r = await client.post(
        f"/api/v1/books/chapters/{r.json()['id']}/scenes/generate-planned",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 422


async def test_generate_planned_is_preflighted(
    client, auth_headers, async_session, test_book, monkeypatch
):
    from app.core import estimates

    _, chapter_id = await _two_beat_chapter(
        client, auth_headers, async_session, test_book
    )
    seen = {}

    async def refuse(db, user_id, est):
        seen["calls"] = est.calls
        return {"allowed": False, "reason": "Needs more than you have."}

    monkeypatch.setattr(estimates, "assess", refuse)
    r = await client.post(
        f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 429
    one = estimates.scene(
        canon_chars=0, cast_context_chars=0, target_words=800, cast_size=2
    )
    assert seen["calls"] == 2 * one.calls  # both scenes, not just the first
    assert (await async_session.execute(select(Job))).scalars().all() == []


async def test_the_chain_runs_in_order_and_each_scene_sees_the_one_before(
    client, auth_headers, async_session, test_book, monkeypatch
):
    import app.core.database as db_mod
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod

    _bind(monkeypatch, async_session)
    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(settings, "LLM_TIER", "free")

    class _Store:
        async def retrieve_similar(self, **kw):
            return []

    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: _Store())
    seen_tails: list[str] = []
    real = prose_mod.run_prose_scene_workflow

    async def spy(scene_id, request, user_id, **kw):
        seen_tails.append(kw.get("prior_scene_tail", ""))
        return await real(scene_id, request, user_id, **kw)

    monkeypatch.setattr(prose_mod, "run_prose_scene_workflow", spy)
    texts = iter(["Firstscene " * 30, "Secondscene " * 30])
    plan = '[{"description": "A beat.", "characters": ["Milo Voss"]}]'

    class _Seq:
        async def generate(self, messages, *, purpose, **kw):
            text = plan if purpose == "plan_beats" else next(texts)
            return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)

    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: _Seq())
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: _Seq())

    _, chapter_id = await _two_beat_chapter(
        client, auth_headers, async_session, test_book
    )
    r = await client.post(
        f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    assert await _worker().run_once() is True  # scene 1, which enqueues scene 2
    assert await _worker().run_once() is True  # scene 2
    assert await _worker().run_once() is False  # the chain is done

    scenes = (
        (
            await async_session.execute(
                select(Scene)
                .where(Scene.chapter_id == uuid.UUID(chapter_id))
                .order_by(Scene.position)
            )
        )
        .scalars()
        .all()
    )
    assert [s.status for s in scenes] == ["completed", "completed"]
    assert [s.position for s in scenes] == [0, 1]
    assert scenes[1].scene_description.startswith("Lunch")
    assert seen_tails[0] == ""
    assert "Firstscene" in seen_tails[1]  # scene 2 started from scene 1
    jobs = (await async_session.execute(select(Job))).scalars().all()
    assert sorted(j.dedupe_key.rsplit(":", 1)[1] for j in jobs) == ["0", "1"]
    assert all(j.status == "succeeded" for j in jobs)

    # Both beats are written: a second click has nothing to do.
    r = await client.post(
        f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
        json={},
        headers=auth_headers,
    )
    assert r.json()["status"] == "nothing_to_do"


async def test_a_failed_scene_ends_the_chain(async_session, test_book, monkeypatch):
    """Only a completed scene enqueues the next: the handler never reaches the
    chain step on failure, so nothing further is spent."""
    import app.jobs.handlers as handlers_mod

    called = []
    monkeypatch.setattr(
        scene_batch,
        "enqueue_next_planned_scene",
        AsyncMock(side_effect=lambda p: called.append(p)),
    )
    monkeypatch.setattr(
        "app.orchestration.prose.run_prose_scene_workflow",
        AsyncMock(return_value={"status": "failed", "error": "boom"}),
    )
    monkeypatch.setattr(
        handlers_mod,
        "_scene_context",
        AsyncMock(return_value={"chapter_summary": "", "story_position": None}),
    )
    monkeypatch.setattr(
        handlers_mod,
        "current_job",
        lambda: types.SimpleNamespace(checkpoint=None, save=None, note=None),
    )
    payload = {
        "scene_id": str(uuid.uuid4()),
        "request": {},
        "user_id": str(test_book.user_id),
        "book_id": str(test_book.id),
        "batch": {"remaining": [{"scene_description": "next"}]},
    }
    with pytest.raises(handlers_mod.JobExecutionError):
        await handlers_mod._run_generate_prose_scene(payload)
    assert called == []


async def test_a_late_cancel_stops_the_chain(async_session, test_book, monkeypatch):
    """A cancel after the workflow's last checkpoint: the scene completes, but
    the enqueue (which reads the job's flag in its own transaction) stops."""
    import app.core.database as db_mod

    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    chapter = Chapter(book_id=test_book.id, title="Live", position=0)
    current = Job(
        kind="generate_prose_scene",
        payload={},
        user_id=test_book.user_id,
        status="running",
        cancel_requested=True,
    )
    async_session.add_all([chapter, current])
    await async_session.commit()
    payload = {
        "user_id": str(test_book.user_id),
        "book_id": str(test_book.id),
        "batch": {
            "key": "k",
            "chapter_id": str(chapter.id),
            "index": 0,
            "remaining": [{"scene_description": "next"}],
        },
    }
    assert await scene_batch.enqueue_next_planned_scene(payload, current.id) is None
    assert [j.id for j in (await async_session.execute(select(Job))).scalars()] == [
        current.id
    ]

    current.cancel_requested = False  # not cancelled: the chain goes on
    await async_session.commit()
    assert await scene_batch.enqueue_next_planned_scene(payload, current.id)
    assert len((await async_session.execute(select(Job))).scalars().all()) == 2


async def test_cancelling_a_batch_scene_cancels_its_queued_successor(
    client, auth_headers, async_session, test_book
):
    """The other order of the race: the next scene was enqueued before the
    cancel landed. Cancelling the running scene cancels the queued one too."""
    batch = {"key": "scene-batch:u:c", "chapter_id": str(uuid.uuid4())}
    running = Job(
        kind="generate_prose_scene",
        payload={"scene_id": str(uuid.uuid4()), "batch": {**batch, "index": 0}},
        user_id=test_book.user_id,
        status="running",
        dedupe_key="scene-batch:u:c:0",
    )
    successor = Job(
        kind="generate_prose_scene",
        payload={"scene_id": str(uuid.uuid4()), "batch": {**batch, "index": 1}},
        user_id=test_book.user_id,
        status="queued",
        dedupe_key="scene-batch:u:c:1",
    )
    other_batch = Job(
        kind="generate_prose_scene",
        payload={},
        user_id=test_book.user_id,
        status="queued",
        dedupe_key="scene-batch:u:other:1",
    )
    async_session.add_all([running, successor, other_batch])
    await async_session.commit()
    r = await client.post(f"/api/v1/jobs/{running.id}/cancel", headers=auth_headers)
    assert r.status_code == 200, r.text
    for job in (running, successor, other_batch):
        await async_session.refresh(job)
    assert running.cancel_requested is True
    assert successor.status == "cancelled"
    assert other_batch.status == "queued"


async def test_the_chain_stops_when_the_chapter_was_trashed(
    async_session, test_book, monkeypatch
):
    import app.core.database as db_mod
    from datetime import datetime, timezone

    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    chapter = Chapter(
        book_id=test_book.id,
        title="Gone",
        position=0,
        deleted_at=datetime.now(timezone.utc),
    )
    async_session.add(chapter)
    await async_session.commit()
    payload = {
        "user_id": str(test_book.user_id),
        "book_id": str(test_book.id),
        "batch": {
            "key": "k",
            "chapter_id": str(chapter.id),
            "index": 0,
            "remaining": [{"scene_description": "next"}],
        },
    }
    assert await scene_batch.enqueue_next_planned_scene(payload) is None
    assert (await async_session.execute(select(Job))).scalars().all() == []


def test_generate_planned_is_in_the_llm_rate_limit_class():
    from app.core.rate_limit import ROUTE_CLASSES

    key = ("POST", "/api/v1/books/chapters/{chapter_id}/scenes/generate-planned")
    assert ROUTE_CLASSES.get(key) == "llm"
    from app.main import app

    assert any(getattr(r, "path", None) == key[1] for r in app.routes)
