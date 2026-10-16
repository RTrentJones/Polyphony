"""SPEC-19 (backlog B3): AI revision of a scene span, backend.

The revise endpoint is preflighted and deduped like every LLM endpoint, the job
writes a PROPOSAL (never the draft), the text outside the span is
byte-identical by construction, accept snapshots the draft first and refuses a
stale proposal, discard changes nothing, and the job's lifecycle (quota pause,
cancel, death) never touches the draft.
"""

import dataclasses
import types
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.llm_text import STORY_MATERIAL_NOTICE
from app.core.orm_models import (
    Chapter,
    Character,
    Job,
    Scene,
    SceneBeat,
    SceneRevision,
    StyleGuide,
)
from app.jobs import repository as jobs_repo
from app.jobs import revise as revise_job
from app.jobs.handlers import Handler
from app.jobs.worker import JobWorker
from app.llm.errors import QuotaExhaustedError
from app.orchestration import revise as rv
from app.core.prompt_markers import POV_LIMIT_OPEN, POV_LINE_RE
from app.orchestration.pov import Pov

pytestmark = pytest.mark.unit

P1 = "Milo kept his eyes on the ledger. The numbers would not add up."
P2 = (
    'Zara leaned over the desk. "You missed a line," she said, and smiled '
    "without warmth. Milo did not answer."
)
P3 = "Outside, the lifts sighed between floors."
DRAFT = f"{P1}\n\n{P2}\n\n{P3}"
VOICE = "I'll not be hurried."


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class FakeLLM:
    """Records every prompt; answers revise calls from a script."""

    def __init__(self, answers=None):
        self.prompts: list[str] = []
        self.purposes: list[str] = []
        self.answers = list(answers or [])

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.append(messages[0]["content"])
        self.purposes.append(purpose)
        answer = self.answers.pop(0) if self.answers else "Zara stayed silent."
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = await answer()
        return types.SimpleNamespace(text=answer, tokens_in=1, tokens_out=1)


class FakeStore:
    async def retrieve_similar(self, **kw):
        return [
            {"id": "chunk-1", "text": VOICE, "score": 0.4, "chunk_type": "dialogue"}
        ]


async def _none():
    return None


def _bind(monkeypatch, session, fake):
    import app.characters.context as ctx_mod
    import app.jobs.handlers as handlers_mod
    import app.jobs.repository as repo_mod
    import app.jobs.worker as worker_mod
    from app.core import estimates

    for mod in (worker_mod, handlers_mod, repo_mod, revise_job):
        monkeypatch.setattr(mod, "get_async_session", lambda: _Ctx(session))
    monkeypatch.setattr(rv, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: FakeStore())
    monkeypatch.setattr(estimates, "requests_left_today", _none)
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    # The registration lives in app/jobs/handlers.py; built here so these tests
    # do not depend on it.
    handler = Handler(
        run=revise_job.run,
        on_dead=revise_job.on_dead,
        on_pause=revise_job.on_pause,
        on_resume=revise_job.on_resume,
        on_cancel=revise_job.on_cancel,
    )
    monkeypatch.setattr(worker_mod, "HANDLERS", {revise_job.KIND: handler})
    return handler


def _worker():
    return JobWorker(
        poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="t"
    )


@pytest.fixture
async def story(async_session, test_book, test_user):
    milo = Character(
        book_id=test_book.id, user_id=test_user.id, name="Milo Voss", role="protagonist"
    )
    zara = Character(
        book_id=test_book.id,
        user_id=test_user.id,
        name="Zara Okafor",
        role="deuteragonist",
    )
    async_session.add_all(
        [milo, zara, StyleGuide(book_id=test_book.id, pov="third", tense="past")]
    )
    chapter = Chapter(
        book_id=test_book.id, title="One", position=0, summary="Audit week begins."
    )
    async_session.add(chapter)
    await async_session.flush()
    scene = Scene(
        user_id=test_user.id,
        book_id=test_book.id,
        chapter_id=chapter.id,
        position=0,
        status="completed",
        generated_content=DRAFT,
        content=DRAFT,
        word_count=len(DRAFT.split()),
        character_ids=[str(milo.id), str(zara.id)],
        characters=["Milo Voss", "Zara Okafor"],
        scene_request={
            "character_ids": [str(milo.id), str(zara.id)],
            "characters": ["Milo Voss", "Zara Okafor"],
            "scene_description": "Milo and Zara at the desk.",
            "setting": "Reconciliations",
            "emotional_tone": "wry",
        },
    )
    async_session.add(scene)
    await async_session.flush()
    for i, text in enumerate((P1, P2, P3)):
        async_session.add(
            SceneBeat(
                scene_id=scene.id, beat_number=i, description=f"b{i}", content=text
            )
        )
    await async_session.commit()
    return types.SimpleNamespace(
        book=test_book, chapter=chapter, scene=scene, milo=milo, zara=zara
    )


def _url(scene, tail="revise"):
    return f"/api/v1/books/scenes/{scene.id}/{tail}"


async def _revise(client, headers, scene, **body):
    body.setdefault("instruction", "Make Zara colder.")
    return await client.post(_url(scene), json=body, headers=headers)


async def _proposals(client, headers, scene):
    r = await client.get(_url(scene, "proposals"), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _revisions(session, scene, source=None):
    q = select(SceneRevision).where(SceneRevision.scene_id == scene.id)
    if source:
        q = q.where(SceneRevision.source == source)
    return list((await session.execute(q)).scalars().all())


def _p2_range():
    start = DRAFT.index(P2)
    return {"start": start, "end": start + len(P2)}


# --- pure helpers -------------------------------------------------------------------


def test_splice_keeps_everything_outside_the_span_byte_identical():
    draft = "  Opening line.\n\nMiddle bit here. \n\n\tEndé \U0001f600 trailing  "
    start = draft.index("Middle")
    end = draft.index("\tEnd")
    span = rv.Span(start, end)
    out = rv.splice(draft, span, "  ```\nNEW middle.\n```  ")
    assert out[:start] == draft[:start]
    assert out.endswith(draft[end:])
    # The span's own edge whitespace (" \n\n") is kept; the model's is not.
    assert out == draft[:start] + "```\nNEW middle.\n```" + " \n\n" + draft[end:]


def test_clean_revision_strips_fences_tags_and_label():
    raw = "```text\n<target>\nRevised passage: She waited.\n</target>\n```"
    assert rv.clean_revision(raw) == "She waited."


def test_resolve_span_modes():
    assert rv.resolve_span(DRAFT) == rv.Span(0, len(DRAFT))
    at = DRAFT.index(P2)
    assert rv.resolve_span(DRAFT, beat_text=P2 + "\n") == rv.Span(at, at + len(P2))
    with pytest.raises(rv.SpanError) as e:
        rv.resolve_span(DRAFT, beat_text="A beat the author rewrote.")
    assert e.value.status == 409 and "select the text instead" in str(e.value)
    with pytest.raises(rv.SpanError) as e:
        rv.resolve_span(DRAFT + "\n\n" + P2, beat_text=P2)
    assert e.value.status == 409 and "appears more than once" in str(e.value)
    with pytest.raises(rv.SpanError) as e:
        rv.resolve_span(DRAFT, range_=(5, len(DRAFT) + 1))
    assert e.value.status == 422
    with pytest.raises(rv.SpanError):
        rv.resolve_span("")


def _canon(terms: set[str], synopsis: str = ""):
    return types.SimpleNamespace(synopsis=synopsis, canon_terms=lambda: terms)


def test_new_names_warns_on_an_invented_name_only():
    """W1-S7: one unknown_proper_nouns call, the draft as known text."""
    known = {"Milo Voss", "Milo", "Voss", "Zara Okafor", "Zara", "Okafor"}
    # "Outside" is capitalised in the draft; "Numbers" the draft uses in
    # lowercase ("The numbers would not add up").
    canon = _canon(known)
    assert rv.new_names("Outside, Zara said nothing. Numbers held.", DRAFT, canon) == []
    assert rv.new_names("Bartholomew Quince walked in.", DRAFT, canon) == [
        "Bartholomew Quince"
    ]


def test_new_names_ignores_ordinary_sentence_openers():
    """NB-1: the reviewer's probe, through the revise path."""
    from tests.unit.test_fidelity_gate import PROBE_DRAFT, PROBE_FLAGGED, PROBE_QUIET

    for text in PROBE_QUIET:
        assert rv.new_names(text, PROBE_DRAFT, _canon({"Milo"})) == [], text
    for text, flagged in PROBE_FLAGGED.items():
        assert rv.new_names(text, PROBE_DRAFT, _canon({"Milo"})) == flagged


def test_scene_draft_is_raw_and_the_export_trims_it():
    from app.exports.builder import scene_text

    scene = Scene(content="  Edited.\n", generated_content="Generated.")
    assert scene.draft == "  Edited.\n" and scene_text(scene) == "Edited."
    assert Scene(generated_content="Generated.\n").draft == "Generated.\n"
    assert Scene().draft == "" and scene_text(Scene()) == ""


def test_span_spec_takes_a_range_or_a_beat_never_both():
    from pydantic import ValidationError

    from app.api.estimates import EstimateRequest
    from app.api.revise import ReviseRequest

    both = {"range": {"start": 0, "end": 5}, "beat_index": 0}
    for model, extra in (
        (ReviseRequest, {"instruction": "x"}),
        (EstimateRequest, {"action": "revise"}),
    ):
        assert issubclass(model, rv.SpanSpec)
        with pytest.raises(ValidationError, match="range OR beat_index"):
            model(**both, **extra)
        assert model(beat_index=1, **extra).beat_index == 1


def test_stub_llm_revise_family_derives_the_answer_from_the_target():
    from evals.stub_llm import _answer

    prompt = rv.build_revise_prompt(
        canon_block="Title: T",
        chapter_summary="",
        cast_context="",
        prior=P1,
        target=P2,
        after=P3,
        instruction="tighten",
        pov=Pov("third person limited"),
        story_position=None,
        secondary_block="",
    )
    out = _answer(prompt)
    assert out == P2.split(". ", 1)[1]
    assert P1 not in out and P3 not in out


# --- the endpoint ---------------------------------------------------------------------


async def test_revise_enqueues_one_job_and_dedupes(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM())
    r1 = await _revise(client, auth_headers, story.scene, range=_p2_range())
    r2 = await _revise(client, auth_headers, story.scene, instruction="Other.")
    assert r1.status_code == r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r1.json()["deduplicated"] is False and r2.json()["deduplicated"] is True
    assert r1.json()["estimate"]["calls"] == 1
    jobs = (
        (await async_session.execute(select(Job).where(Job.kind == "revise_scene")))
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert job.domain_type == "scene" and job.domain_id == story.scene.id
    assert job.dedupe_key == f"revise:{story.scene.id}"
    assert job.payload["range"] == [_p2_range()["start"], _p2_range()["end"]]
    assert job.payload["base_text_hash"] == rv.text_hash(DRAFT)
    # Requesting a revision does not touch the draft.
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT


async def test_an_identical_request_racing_past_the_lookup_is_deduplicated(
    client, auth_headers, async_session, story, monkeypatch
):
    """Codex on #38: the loser of a race gets the winner's job, and with an
    identical payload it must still say deduplicated (a double-click)."""
    from app.api import revise as revise_api
    from app.jobs import repository as jobs_repo

    _bind(monkeypatch, async_session, FakeLLM())
    r1 = await _revise(client, auth_headers, story.scene, range=_p2_range())
    assert r1.json()["deduplicated"] is False

    real = jobs_repo.find_active
    calls = {"n": 0}

    async def miss_first(session, key):
        # The endpoint's own lookup misses (the race); enqueue's finds the job.
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(session, key)

    monkeypatch.setattr(revise_api.jobs_repo, "find_active", miss_first)
    r2 = await _revise(client, auth_headers, story.scene, range=_p2_range())
    assert r2.status_code == 200, r2.text
    assert r2.json()["job_id"] == r1.json()["job_id"]
    assert r2.json()["deduplicated"] is True


async def test_revise_is_refused_with_the_estimate_when_over_budget(
    client, auth_headers, async_session, story, monkeypatch
):
    from app.core import estimates

    _bind(monkeypatch, async_session, FakeLLM())

    async def _tiny(db, user_id):
        return 10

    monkeypatch.setattr(estimates, "remaining_budget_24h", _tiny)
    r = await _revise(client, auth_headers, story.scene)
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert detail["estimate"]["allowed"] is False
    assert detail["estimate"]["action"] == "revise"
    assert (await async_session.execute(select(Job))).scalars().first() is None


@pytest.mark.parametrize(
    "body,code",
    [
        ({"range": {"start": 0, "end": 5}, "beat_index": 0}, 422),
        ({"range": {"start": 5, "end": 5}}, 422),
        ({"range": {"start": 0, "end": len(DRAFT) + 1}}, 422),
        ({"instruction": "   "}, 422),
        ({"instruction": "x" * 2001}, 422),
        ({"beat_index": 9}, 404),
        ({"base_text_hash": "0" * 64}, 409),
        ({"range": {"start": 0, "end": 4}, "selected_text": "Zara"}, 409),
    ],
)
async def test_revise_validates_the_span(
    client, auth_headers, async_session, story, monkeypatch, body, code
):
    _bind(monkeypatch, async_session, FakeLLM())
    r = await _revise(client, auth_headers, story.scene, **body)
    assert r.status_code == code, r.text


async def test_revise_refuses_a_scene_still_generating_and_others_scenes(
    client, auth_headers, async_session, story, monkeypatch
):
    from app.core.orm_models import User
    from app.core.security import create_access_token

    _bind(monkeypatch, async_session, FakeLLM())
    other = User(email="other@example.com", hashed_password="x", full_name="O")
    async_session.add(other)
    await async_session.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
    }
    assert (await _revise(client, headers, story.scene)).status_code == 404

    story.scene.status = "processing"
    await async_session.commit()
    r = await _revise(client, auth_headers, story.scene)
    assert r.status_code == 409 and "still being generated" in r.text


async def test_beat_mode_locates_the_beat_and_refuses_an_edited_one(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM())
    r = await _revise(client, auth_headers, story.scene, beat_index=1)
    assert r.status_code == 200, r.text
    assert r.json()["range"] == [_p2_range()["start"], _p2_range()["end"]]
    job = await async_session.get(Job, UUID(r.json()["job_id"]))
    assert job.payload["beat_index"] == 1 and job.payload["beat_description"] == "b1"

    # The author rewrote that paragraph since the beat was generated.
    await jobs_repo.mark_cancelled(async_session, job)
    story.scene.content = DRAFT.replace("You missed a line", "Wrong column")
    await async_session.commit()
    r = await _revise(client, auth_headers, story.scene, beat_index=1)
    assert r.status_code == 409
    assert "the beat was edited; select the text instead" in r.text.lower()


async def test_estimate_endpoint_prices_a_revision(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM())
    r = await client.post(
        "/api/v1/estimate",
        json={"action": "revise", "scene_id": str(story.scene.id), "beat_index": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    whole = await client.post(
        "/api/v1/estimate",
        json={"action": "revise", "scene_id": str(story.scene.id)},
        headers=auth_headers,
    )
    assert r.json()["action"] == "revise" and r.json()["calls"] == 1
    assert r.json()["allowed"] is True
    assert whole.json()["tokens"] >= r.json()["tokens"]
    missing = await client.post(
        "/api/v1/estimate", json={"action": "revise"}, headers=auth_headers
    )
    assert missing.status_code == 404
    # Same span rule as /revise: a range and a beat together are refused.
    both = await client.post(
        "/api/v1/estimate",
        json={
            "action": "revise",
            "scene_id": str(story.scene.id),
            "beat_index": 1,
            "range": _p2_range(),
        },
        headers=auth_headers,
    )
    assert both.status_code == 422


async def test_save_and_accept_snapshot_the_draft_by_one_rule(
    client, auth_headers, async_session, story, monkeypatch
):
    """W1-S6: a manual save and an accepted revision each keep the draft they
    replace, raw, as "generated" until the author has saved over it and
    "edited" after."""
    story.scene.content = None
    story.scene.generated_content = DRAFT + "\n"
    await async_session.commit()
    _bind(monkeypatch, async_session, FakeLLM(["Zara said nothing."]))
    await _run_job(client, auth_headers, async_session, story.scene, range=_p2_range())
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    before = {r.id for r in await _revisions(async_session, story.scene)}
    r = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/v1/books/scenes/{story.scene.id}/content",
        json={"content": "Rewritten by hand."},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    added = [
        (a.source, a.content)
        for a in await _revisions(async_session, story.scene)
        if a.id not in before and a.source != "ai_revise"
    ]
    assert sorted(added) == sorted(
        [("generated", DRAFT + "\n"), ("edited", proposal["content"])]
    )


# --- the job through the worker -------------------------------------------------------


async def _run_job(client, headers, session, scene, **body):
    r = await _revise(client, headers, scene, **body)
    assert r.status_code == 200, r.text
    assert await _worker().run_once() is True
    job = await session.get(Job, UUID(r.json()["job_id"]))
    await session.refresh(job)
    return job


async def test_prompt_carries_canon_cast_and_read_only_surroundings(
    client, auth_headers, async_session, story, monkeypatch
):
    from app.planning.canon import build_canon

    fake = FakeLLM(["Zara leaned in. Nothing warm in it."])
    _bind(monkeypatch, async_session, fake)
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    assert job.status == "succeeded", job.error
    assert fake.purposes == ["scene_revise"]
    prompt = fake.prompts[0]
    assert prompt.startswith("Revise the TARGET passage")
    assert STORY_MATERIAL_NOTICE in prompt
    canon = await build_canon(async_session, story.book.id)
    assert canon.full_block() in prompt  # the WHOLE canon, untruncated
    assert "<chapter>\nAudit week begins.\n</chapter>" in prompt
    assert "<character>" in prompt and VOICE in prompt  # the cast's voice samples
    assert f"<prior>\n{P1}\n</prior>" in prompt
    assert f"<target>\n{P2}\n</target>" in prompt
    assert f"<after>\n{P3}\n</after>" in prompt
    assert "<instruction>\nMake Zara colder.\n</instruction>" in prompt
    assert "of this scene's plan" not in prompt  # a range, not a beat
    # S4: one amended sentence, every revise.
    assert "Keep the events, and the facts, people and objects the surrounding" in (
        " ".join(prompt.split())
    )


async def test_a_beat_revise_prompt_names_the_beat_from_the_plan(
    client, auth_headers, async_session, story, monkeypatch
):
    """Eval finding 6: "redo beat 2" tells the model what beat 2 was."""
    fake = FakeLLM(["Zara leaned in."])
    _bind(monkeypatch, async_session, fake)
    job = await _run_job(client, auth_headers, async_session, story.scene, beat_index=1)
    assert job.status == "succeeded", job.error
    prompt = fake.prompts[0]
    # W3-3: the whole POV paragraph, the limit included, not the bare label.
    assert POV_LINE_RE.search(prompt) and POV_LIMIT_OPEN in prompt
    assert "in this scene counts.\nBeat 2 of this scene's plan: b1\n" in prompt


async def test_whole_scene_revise_has_no_surroundings(
    client, auth_headers, async_session, story, monkeypatch
):
    fake = FakeLLM(["All of it, again."])
    _bind(monkeypatch, async_session, fake)
    job = await _run_job(client, auth_headers, async_session, story.scene)
    assert job.status == "succeeded", job.error
    assert "<prior>" not in fake.prompts[0] and "<after>" not in fake.prompts[0]
    assert "The target opens the scene." in fake.prompts[0]
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    assert proposal["content"] == "All of it, again."


async def test_revise_then_accept_keeps_outside_text_and_snapshots_once(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM(["  Zara said nothing.  "]))
    rng = _p2_range()
    job = await _run_job(client, auth_headers, async_session, story.scene, range=rng)
    assert job.status == "succeeded", job.error

    # The job proposes; the draft is untouched until the author accepts.
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT
    listing = await _proposals(client, auth_headers, story.scene)
    assert listing["draft"] == DRAFT and listing["draft_hash"] == rv.text_hash(DRAFT)
    [proposal] = listing["proposals"]
    assert proposal["status"] == "proposed" and proposal["stale"] is False
    assert proposal["instruction"] == "Make Zara colder."
    assert proposal["job_id"] == str(job.id)
    assert proposal["range"] == [rng["start"], rng["end"]]
    expected = DRAFT[: rng["start"]] + "Zara said nothing." + DRAFT[rng["end"] :]
    assert proposal["content"] == expected
    assert expected[slice(*proposal["revised_range"])] == "Zara said nothing."
    assert proposal["warnings"] == []

    before = await _revisions(async_session, story.scene)
    r = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert r.status_code == 200, r.text
    await async_session.refresh(story.scene)
    content = story.scene.content
    assert content[: rng["start"]] == DRAFT[: rng["start"]]
    assert content[-(len(DRAFT) - rng["end"]) :] == DRAFT[rng["end"] :]
    assert content == expected
    after = await _revisions(async_session, story.scene)
    added = [a for a in after if a.id not in {b.id for b in before}]
    assert [(a.source, a.content) for a in added] == [("edited", DRAFT)]
    accepted = await async_session.get(SceneRevision, UUID(proposal["id"]))
    await async_session.refresh(accepted)
    assert accepted.meta["status"] == "accepted"

    again = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert again.status_code == 409


async def test_accept_keeps_edge_whitespace_outside_the_span(
    client, auth_headers, async_session, story, monkeypatch
):
    """Codex on #38: the draft is the raw editable text, so an interior
    revision keeps a trailing newline and the snapshot restores it exactly."""
    raw = DRAFT + "\n"
    story.scene.content = raw
    await async_session.commit()
    _bind(monkeypatch, async_session, FakeLLM(["Zara said nothing."]))
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    assert job.status == "succeeded", job.error
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    assert proposal["content"].endswith("\n")
    before = await _revisions(async_session, story.scene)
    r = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert r.status_code == 200, r.text
    await async_session.refresh(story.scene)
    assert story.scene.content.endswith(DRAFT[_p2_range()["end"] :] + "\n")
    added = [
        a
        for a in await _revisions(async_session, story.scene)
        if a.id not in {b.id for b in before}
    ]
    assert [a.content for a in added] == [raw]


async def test_discard_leaves_the_draft_byte_identical(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM(["Something else entirely."]))
    await _run_job(client, auth_headers, async_session, story.scene, range=_p2_range())
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    count = len(await _revisions(async_session, story.scene))
    r = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/discard"), headers=auth_headers
    )
    assert r.status_code == 200, r.text
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT
    assert len(await _revisions(async_session, story.scene)) == count
    [listed] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    assert listed["status"] == "discarded"
    # Proposals are not restorable history: the revisions list leaves them out.
    history = await client.get(
        f"/api/v1/books/scenes/{story.scene.id}/revisions", headers=auth_headers
    )
    assert all(r["source"] != "ai_revise" for r in history.json()["revisions"])
    assert proposal["id"] not in {r["id"] for r in history.json()["revisions"]}
    again = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert again.status_code == 409


async def test_accept_after_a_concurrent_edit_is_a_conflict(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM(["Zara said nothing."]))
    await _run_job(client, auth_headers, async_session, story.scene, range=_p2_range())
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    edited = DRAFT + "\n\nA line the author added meanwhile."
    r = await client.put(
        f"/api/v1/books/scenes/{story.scene.id}/content",
        json={"content": edited},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert (await _proposals(client, auth_headers, story.scene))["proposals"][0][
        "stale"
    ] is True
    count = len(await _revisions(async_session, story.scene))
    r = await client.post(
        _url(story.scene, f"revisions/{proposal['id']}/accept"), headers=auth_headers
    )
    assert r.status_code == 409, r.text
    await async_session.refresh(story.scene)
    assert story.scene.content == edited
    assert len(await _revisions(async_session, story.scene)) == count


async def test_a_draft_edited_while_the_job_waited_fails_the_job_not_the_draft(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM())
    r = await _revise(client, auth_headers, story.scene, range=_p2_range())
    story.scene.content = DRAFT + " Edited."
    await async_session.commit()
    assert await _worker().run_once() is True
    job = await async_session.get(Job, UUID(r.json()["job_id"]))
    await async_session.refresh(job)
    assert job.status == "dead" and job.error == revise_job.DRAFT_EDITED
    assert await _revisions(async_session, story.scene, "ai_revise") == []
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT + " Edited."


async def test_an_invented_name_is_a_warning_not_a_failure(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM(["Bartholomew Quince took the pen."]))
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    assert job.status == "succeeded"
    [proposal] = (await _proposals(client, auth_headers, story.scene))["proposals"]
    assert proposal["status"] == "proposed"
    assert proposal["warnings"] and "Bartholomew Quince" in proposal["warnings"][0]


# --- lifecycle ------------------------------------------------------------------------


async def test_quota_exhaustion_pauses_then_resumes_without_touching_the_draft(
    client, auth_headers, async_session, story, monkeypatch
):
    fake = FakeLLM([QuotaExhaustedError("429", reset_after=60), "Zara waited."])
    _bind(monkeypatch, async_session, fake)
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    assert job.status == "queued" and job.paused_since is not None
    assert job.pause_count == 1
    await async_session.refresh(story.scene)
    assert story.scene.status == "completed" and story.scene.content == DRAFT
    assert await _revisions(async_session, story.scene, "ai_revise") == []

    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await async_session.commit()
    assert await _worker().run_once() is True
    await async_session.refresh(job)
    assert job.status == "succeeded", job.error
    [proposal] = await _revisions(async_session, story.scene, "ai_revise")
    assert proposal.meta["status"] == "proposed"
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT


async def test_cancel_during_the_call_stores_nothing(
    client, auth_headers, async_session, story, monkeypatch
):
    holder = {}

    async def answer_after_cancel():
        r = await client.post(
            f"/api/v1/jobs/{holder['job_id']}/cancel", headers=auth_headers
        )
        assert r.json()["cancel_requested"] is True
        return "Zara waited."

    _bind(monkeypatch, async_session, FakeLLM([answer_after_cancel]))
    r = await _revise(client, auth_headers, story.scene, range=_p2_range())
    holder["job_id"] = r.json()["job_id"]
    assert await _worker().run_once() is True
    job = await async_session.get(Job, UUID(holder["job_id"]))
    await async_session.refresh(job)
    assert job.status == "cancelled"
    assert await _revisions(async_session, story.scene, "ai_revise") == []
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT and story.scene.status == "completed"


async def test_cancel_and_dead_hooks_mark_this_jobs_proposal_only(
    client, auth_headers, async_session, story, monkeypatch
):
    from app.jobs.context import JobContext, job_context

    handler = _bind(monkeypatch, async_session, FakeLLM(["Zara waited."]))
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    [proposal] = await _revisions(async_session, story.scene, "ai_revise")
    decided = SceneRevision(
        scene_id=story.scene.id,
        content="x",
        source="ai_revise",
        meta={"job_id": str(job.id), "status": "accepted"},
    )
    async_session.add(decided)
    await async_session.commit()
    payload = dict(job.payload)

    with job_context(JobContext(job_id=job.id, kind=revise_job.KIND)):
        await handler.on_cancel(payload)
        await handler.on_pause(payload)
        await handler.on_resume(payload)
    await async_session.refresh(proposal)
    await async_session.refresh(decided)
    assert proposal.meta["status"] == "cancelled"
    assert decided.meta["status"] == "accepted"  # the author's decision stands

    proposal.meta = {**proposal.meta, "status": "proposed"}
    await async_session.commit()
    with job_context(JobContext(job_id=job.id, kind=revise_job.KIND, error="x")):
        await handler.on_dead(payload)
        await handler.on_dead({})  # a hook never fails on an empty payload
    await async_session.refresh(proposal)
    assert proposal.meta["status"] == "failed"
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT and story.scene.status == "completed"


async def test_a_failed_call_kills_the_job_and_marks_nothing(
    client, auth_headers, async_session, story, monkeypatch
):
    _bind(monkeypatch, async_session, FakeLLM([RuntimeError("provider said no")]))
    job = await _run_job(
        client, auth_headers, async_session, story.scene, range=_p2_range()
    )
    assert job.status == "dead" and "provider said no" in job.error
    await async_session.refresh(story.scene)
    assert story.scene.content == DRAFT and story.scene.status == "completed"


async def test_the_job_kind_has_every_lifecycle_hook(monkeypatch, async_session):
    handler = _bind(monkeypatch, async_session, FakeLLM())
    for hook in ("on_dead", "on_pause", "on_resume", "on_cancel"):
        assert getattr(handler, hook) is not None
    assert dataclasses.is_dataclass(handler)


def test_new_names_knows_what_the_synopsis_says():
    """M5: revise checks the outline audit's known text (the canon's synopsis
    and bible) plus the draft, not the draft alone."""
    canon = _canon({"Milo"}, "He felt seen. He felt, God help him, useful.")
    assert rv.new_names("He felt, God help him, seen.", DRAFT, canon) == []
    assert rv.new_names("He felt, God help him, seen.", DRAFT, _canon({"Milo"})) == [
        "God"
    ]
