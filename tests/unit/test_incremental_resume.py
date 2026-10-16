"""Incremental resume for multi-call jobs (SPEC-08).

A resumed job continues from its last completed model call: the prose scene
from its last beat, the staged outline from its last stage/act, the ensemble
from its last agent call. A canon change during the pause restarts the job and
says why; a cancelled job leaves no checkpoint and no half-written scene.
"""

import json
import types
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.orm_models import Chapter, Character, Job, Scene, SceneBeat
from app.jobs import repository as jobs_repo
from app.jobs.worker import JobWorker
from app.llm.errors import QuotaExhaustedError
from app.orchestration import ensemble as ens
from app.planning import staged_outline as so
from tests.unit.factories import make_scope

pytestmark = pytest.mark.unit

BEATS_TOTAL = 5


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class CountingLLM:
    """Counts calls by purpose; raises quota on the Nth call of one purpose."""

    def __init__(self, texts: dict, fail_on: tuple[str, int] | None = None):
        self.texts = texts
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def generate(self, messages, *, purpose, **kw):
        if self.fail_on and purpose == self.fail_on[0]:
            if self.calls.count(purpose) + 1 == self.fail_on[1]:
                self.fail_on = None  # quota returns after the pause
                raise QuotaExhaustedError("429", reset_after=60)
        self.calls.append(purpose)
        val = self.texts.get(purpose, "Prose. " * 20)
        text = val.pop(0) if isinstance(val, list) else val
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)

    def reset(self):
        self.calls = []


class _NoStore:
    async def retrieve_similar(self, **kw):
        return []


def _plan_text(names):
    return json.dumps(
        [
            {"description": f"Beat {i + 1}: something changes.", "characters": names}
            for i in range(BEATS_TOTAL)
        ]
    )


def _bind(monkeypatch, session, fake):
    import app.characters.context as ctx_mod
    import app.jobs.handlers as handlers_mod
    import app.jobs.repository as repo_mod
    import app.jobs.worker as worker_mod
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod

    for mod in (worker_mod, handlers_mod, repo_mod, prose_mod):
        monkeypatch.setattr(mod, "get_async_session", lambda: _Ctx(session))
    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: _NoStore())
    monkeypatch.setattr(settings, "LLM_TIER", "free")


def _worker():
    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="t"
    )


async def _due(session, job):
    await session.refresh(job)
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()


async def _prose_job(session, user, book, fail_on):
    milo = Character(book_id=book.id, user_id=user.id, name="Milo Voss")
    session.add(milo)
    ch = Chapter(book_id=book.id, title="One", position=0, summary="Day one.")
    session.add(ch)
    await session.flush()
    request = {
        "scene_description": "Milo starts work.",
        "setting": "office",
        "emotional_tone": "dry",
        "characters": ["Milo Voss"],
        "character_ids": [str(milo.id)],
        "target_word_count": 500,
    }
    scene = Scene(
        user_id=user.id,
        chapter_id=ch.id,
        position=0,
        status="processing",
        scene_request=request,
    )
    session.add(scene)
    await session.flush()
    job = await jobs_repo.enqueue(
        session,
        kind="generate_prose_scene",
        payload={
            "scene_id": str(scene.id),
            "request": request,
            "user_id": str(user.id),
            "book_id": str(book.id),
        },
        user_id=user.id,
        domain=("scene", scene.id),
    )
    await session.commit()
    fake = CountingLLM({"plan_beats": _plan_text(["Milo Voss"])}, fail_on=fail_on)
    return types.SimpleNamespace(job=job, scene=scene, milo=milo, fake=fake)


async def _beat_rows(session, scene):
    return (
        await session.execute(
            select(func.count())
            .select_from(SceneBeat)
            .where(SceneBeat.scene_id == scene.id)
        )
    ).scalar_one()


# --- AC1 ------------------------------------------------------------------------------


async def test_ac1_prose_resume_spends_only_the_remaining_beats(
    async_session, test_user, test_book, monkeypatch
):
    t = await _prose_job(async_session, test_user, test_book, ("beat_prose", 3))
    _bind(monkeypatch, async_session, t.fake)

    assert await _worker().run_once() is True  # beats 1-2 done, beat 3 hits quota
    await async_session.refresh(t.job)
    assert t.job.status == "queued" and t.job.paused_since is not None
    assert t.fake.calls.count("plan_beats") == 1
    assert len(t.job.checkpoint["prose"]) == 2
    assert t.job.checkpoint["progress"] == {
        "stage": "beats",
        "done": 2,
        "total": BEATS_TOTAL,
    }
    await async_session.refresh(t.scene)
    assert t.scene.status == "paused"

    t.fake.reset()
    await _due(async_session, t.job)
    assert await _worker().run_once() is True
    assert t.fake.calls.count("plan_beats") == 0  # no planner call on resume
    assert t.fake.calls.count("beat_prose") == BEATS_TOTAL - 2
    await async_session.refresh(t.job)
    assert t.job.status == "succeeded"
    assert t.job.checkpoint is None
    assert t.job.error is None  # the pause note does not outlive the pause
    await async_session.refresh(t.scene)
    assert t.scene.status == "completed"
    assert await _beat_rows(async_session, t.scene) == BEATS_TOTAL


async def test_job_api_exposes_progress(
    client, auth_headers, async_session, test_user, test_book, monkeypatch
):
    t = await _prose_job(async_session, test_user, test_book, ("beat_prose", 3))
    _bind(monkeypatch, async_session, t.fake)
    await _worker().run_once()
    body = (await client.get(f"/api/v1/jobs/{t.job.id}", headers=auth_headers)).json()
    assert body["status"] == "paused"
    assert body["progress"]["done"] == 2
    assert body["progress"]["total"] == BEATS_TOTAL


# --- AC2 ------------------------------------------------------------------------------

_SKELETON = (
    '{"premise_restated":"Milo Voss vs Edric Thane","central_conflict":"c",'
    '"acts":[{"title":"A","goal":"g","turn":"t","chapters_planned":1},'
    '{"title":"B","goal":"g","turn":"t","chapters_planned":1}]}'
)
_CHAPTERS = (
    '[{"title":"Ch1","summary":"s","act_index":0,"pov":"Milo Voss",'
    '"characters":["Milo Voss"],"threads":[]},'
    '{"title":"Ch2","summary":"s","act_index":1,"pov":"Milo Voss",'
    '"characters":["Milo Voss"],"threads":[]}]'
)


def _beats(title):
    return (
        f'[{{"title":"{title}","summary":"Milo Voss faces Edric Thane",'
        '"children":[{"title":"b","summary":"x","children":[]}]}]'
    )


def _canon():
    from app.planning.canon import Canon

    chars = [
        types.SimpleNamespace(
            name=n,
            role=r,
            description=None,
            personality_traits=None,
            goals=None,
            arc=None,
            backstory=None,
            voice_characteristics=None,
            relationships=None,
        )
        for n, r in [("Milo Voss", "protagonist"), ("Edric Thane", "antagonist")]
    ]
    return Canon(title="Bored to Undeath", synopsis="x", characters=chars)


def _outline_llm(fail_on):
    return CountingLLM(
        {
            "outline:skeleton": _SKELETON,
            "outline:chapters": _CHAPTERS,
            "outline:beats": [_beats("Ch1"), _beats("Ch2")],
        },
        fail_on=fail_on,
    )


async def test_ac2_staged_outline_resumes_after_chapters(monkeypatch):
    fake = _outline_llm(("outline:beats", 1))  # paused right after chapters
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    saved: dict = {}

    async def save(data):
        saved.clear()
        saved.update(json.loads(json.dumps(data)))

    with pytest.raises(QuotaExhaustedError):
        await so.generate_staged_outline(_canon(), save_checkpoint=save)
    assert fake.calls == ["outline:skeleton", "outline:chapters"]
    assert "skeleton" in saved and "chapters" in saved["passes"][0]

    fake.reset()
    nodes, warnings = await so.generate_staged_outline(
        _canon(), checkpoint=saved, save_checkpoint=save
    )
    assert "outline:skeleton" not in fake.calls
    assert "outline:chapters" not in fake.calls
    assert fake.calls == ["outline:beats", "outline:beats"]  # one per act
    assert [n["title"] for n in nodes] == ["Ch1", "Ch2"]


async def test_staged_outline_resumes_mid_act(monkeypatch):
    fake = _outline_llm(("outline:beats", 2))  # act 0 done, act 1 hits quota
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    saved: dict = {}

    async def save(data):
        saved.clear()
        saved.update(json.loads(json.dumps(data)))

    with pytest.raises(QuotaExhaustedError):
        await so.generate_staged_outline(_canon(), save_checkpoint=save)
    assert list(saved["passes"][0]["acts"]) == ["0"]
    assert saved["progress"]["done"] == 3  # skeleton + chapters + act 0

    fake.reset()
    nodes, _ = await so.generate_staged_outline(
        _canon(), checkpoint=saved, save_checkpoint=save
    )
    assert fake.calls == ["outline:beats"]  # only the unfinished act
    assert [n["title"] for n in nodes] == ["Ch1", "Ch2"]


# --- AC3 ------------------------------------------------------------------------------


async def test_ac3_canon_edit_during_pause_restarts_and_says_why(
    async_session, test_user, test_book, monkeypatch
):
    t = await _prose_job(async_session, test_user, test_book, ("beat_prose", 3))
    _bind(monkeypatch, async_session, t.fake)
    await _worker().run_once()
    await async_session.refresh(t.job)
    assert len(t.job.checkpoint["prose"]) == 2

    # The author edits a character while the job waits for quota.
    t.milo.arc = "from numb to awake"
    await async_session.commit()

    t.fake.reset()
    await _due(async_session, t.job)
    assert await _worker().run_once() is True
    assert t.fake.calls.count("plan_beats") == 1  # restarted from scratch
    assert t.fake.calls.count("beat_prose") == BEATS_TOTAL
    await async_session.refresh(t.job)
    assert t.job.status == "succeeded"
    assert "canon changed" in (t.job.error or "")


async def test_staged_outline_canon_change_restarts(monkeypatch):
    fake = _outline_llm(("outline:beats", 1))
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    saved: dict = {}
    notes: list[str] = []

    async def save(data):
        saved.clear()
        saved.update(json.loads(json.dumps(data)))

    async def note(msg):
        notes.append(msg)

    with pytest.raises(QuotaExhaustedError):
        await so.generate_staged_outline(_canon(), save_checkpoint=save)
    edited = _canon()
    edited.synopsis = "an edited synopsis"
    fake.reset()
    await so.generate_staged_outline(
        edited, checkpoint=saved, save_checkpoint=save, on_restart=note
    )
    assert fake.calls[:2] == ["outline:skeleton", "outline:chapters"]
    assert notes and "canon changed" in notes[0]


# --- AC4 ------------------------------------------------------------------------------


async def test_ac4_cancelled_paused_job_drops_checkpoint_and_writes_no_beats(
    client, auth_headers, async_session, test_user, test_book, monkeypatch
):
    t = await _prose_job(async_session, test_user, test_book, ("beat_prose", 3))
    _bind(monkeypatch, async_session, t.fake)
    await _worker().run_once()
    await async_session.refresh(t.job)
    assert t.job.checkpoint is not None

    r = await client.post(f"/api/v1/jobs/{t.job.id}/cancel", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    await async_session.refresh(t.job)
    assert t.job.checkpoint is None
    await async_session.refresh(t.scene)
    assert t.scene.status == "cancelled"
    assert await _beat_rows(async_session, t.scene) == 0
    # A cancelled job is never claimed again.
    await _due(async_session, t.job)
    assert await _worker().run_once() is False


async def test_ac4_cancel_while_running_stops_at_the_next_beat(
    async_session, test_user, test_book, monkeypatch
):
    t = await _prose_job(async_session, test_user, test_book, None)
    _bind(monkeypatch, async_session, t.fake)
    real = t.fake.generate

    async def generate(messages, *, purpose, **kw):
        if purpose == "beat_prose" and t.fake.calls.count("beat_prose") == 1:
            job = await async_session.get(Job, t.job.id)
            job.cancel_requested = True  # the author clicks Cancel mid-beat
            await async_session.commit()
        return await real(messages, purpose=purpose, **kw)

    t.fake.generate = generate
    assert await _worker().run_once() is True
    assert t.fake.calls.count("beat_prose") == 2  # stopped at the seam after it
    await async_session.refresh(t.job)
    assert t.job.status == "cancelled"
    assert t.job.checkpoint is None
    await async_session.refresh(t.scene)
    assert t.scene.status == "cancelled"
    assert await _beat_rows(async_session, t.scene) == 0


# --- ensemble: per agent call -----------------------------------------------------------


async def test_ensemble_beat_resumes_after_the_last_agent_call(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    fake = CountingLLM(
        {
            "ensemble:proposal": '{"intent":"i","actions":["nods"],"lines":["Hi"]}',
            "ensemble:narration": 'Milo nods. "Hi," he says. Zara watches him.',
            "ensemble:review": '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":["Milo","Zara"],"objections":[]}',
        },
        fail_on=("ensemble:narration", 1),
    )
    monkeypatch.setattr(ens, "get_llm_client", lambda: fake)

    async def _no_chars(names, **kw):
        return {}

    monkeypatch.setattr(ens, "load_characters_for_book", _no_chars)
    saved: dict = {}

    async def save(state):
        saved.clear()
        saved.update(json.loads(json.dumps(state)))

    kw = dict(
        scene_request={"characters": ["Milo", "Zara"], "setting": "office"},
        beat_description="They meet.",
        user_id=None,
        book_id=None,
        canon_context="",
        scope=make_scope(),
        save_state=save,
    )
    with pytest.raises(QuotaExhaustedError):
        await ens.run_ensemble(**kw)
    assert len(saved["proposals"]) == 2

    fake.reset()
    out = await ens.run_ensemble(state=saved, **kw)
    assert "ensemble:proposal" not in fake.calls  # both proposals were kept
    assert fake.calls == ["ensemble:narration", "ensemble:review"]
    assert out["evaluation"]["converged"] is True


async def test_handler_recomputes_derived_inputs_from_the_db(
    async_session, test_user, test_book, monkeypatch
):
    """chapter_summary/prior_tail are read at start, so an edit made during a
    pause reaches the resumed run (SPEC-08 §6)."""
    from app.jobs.handlers import _scene_context

    import app.jobs.handlers as handlers_mod

    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    ch = Chapter(book_id=test_book.id, title="One", position=0, summary="Old")
    async_session.add(ch)
    await async_session.flush()
    prev = Scene(
        user_id=test_user.id,
        chapter_id=ch.id,
        position=0,
        status="completed",
        content="the earlier scene ends here",
    )
    scene = Scene(
        user_id=test_user.id, chapter_id=ch.id, position=1, status="processing"
    )
    async_session.add_all([prev, scene])
    await async_session.commit()
    ch.summary = "Edited during the pause"
    await async_session.commit()

    got = await _scene_context(scene.id)
    assert got["chapter_summary"] == "Edited during the pause"
    assert got["prior_scene_tail"].endswith("ends here")


async def test_prior_scene_tail_keeps_paragraph_breaks(
    async_session, test_user, test_book, monkeypatch
):
    """The tail handed to the next scene is prose, not a wall of words: its
    paragraph breaks survive and it opens on a sentence (SPEC-18)."""
    from app.jobs.handlers import _scene_context
    from app.orchestration.prose import PREV_TAIL_WORDS, SceneSetup, _canon_hash
    from app.planning.canon import Canon

    import app.jobs.handlers as handlers_mod

    monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx(async_session))
    ch = Chapter(book_id=test_book.id, title="One", position=0, summary="s")
    async_session.add(ch)
    await async_session.flush()
    paragraph = " ".join(f"Milo counted form {i} twice over." for i in range(40))
    content = "\n\n".join([paragraph] * 4 + ["She left.\n\nThe lights hummed on."])
    prev = Scene(
        user_id=test_user.id,
        chapter_id=ch.id,
        position=0,
        status="completed",
        content=content,
    )
    scene = Scene(
        user_id=test_user.id, chapter_id=ch.id, position=1, status="processing"
    )
    async_session.add_all([prev, scene])
    await async_session.commit()

    tail = (await _scene_context(scene.id))["prior_scene_tail"]
    assert tail.endswith("She left.\n\nThe lights hummed on.")
    assert "\n\n" in tail
    assert tail.startswith("Milo counted form ")  # a sentence, not a fragment
    # A checkpoint fingerprinted with the old flat tail still matches.
    legacy = " ".join(content.split()[-PREV_TAIL_WORDS:])
    setup = SceneSetup(Canon("c"), [], None, None, "", [], [])
    assert _canon_hash(setup, "s", None, tail) == _canon_hash(setup, "s", None, legacy)


async def test_editing_the_previous_scene_during_pause_restarts(
    async_session, test_user, test_book, monkeypatch
):
    """The checkpoint fingerprint covers the prior scene's prose: beats written
    after the old tail must not continue from an edited one."""
    t = await _prose_job(async_session, test_user, test_book, ("beat_prose", 3))
    _bind(monkeypatch, async_session, t.fake)
    await _worker().run_once()
    await async_session.refresh(t.job)
    assert len(t.job.checkpoint["prose"]) == 2

    # The author finishes (or edits) the scene before this one while it waits.
    scene = await async_session.get(Scene, UUID(t.job.payload["scene_id"]))
    async_session.add(
        Scene(
            user_id=test_user.id,
            book_id=test_book.id,
            chapter_id=scene.chapter_id,
            position=-1,
            status="completed",
            content="Earlier, Milo had already read every appendix twice.",
        )
    )
    await async_session.commit()

    t.fake.reset()
    await _due(async_session, t.job)
    assert await _worker().run_once() is True
    assert t.fake.calls.count("plan_beats") == 1  # restarted from scratch
    await async_session.refresh(t.job)
    assert t.job.status == "succeeded"
    assert "scene before it was edited" in (t.job.error or "")
