"""W12-5 (SPEC-84): the baseline harness — arm A through the product workflow,
only context assembly differing, cost by job id, blinding, and the statistic."""

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.characters.context import SceneScope
from app.core.orm_models import APIUsage, Job, Scene
from app.jobs.repository import claim_one
from evals.baseline import arms, blind, score
from evals.harness.cache import Cache
from tests.unit.test_scene_canon import FakeLLM, FakeStore, _bind, _Ctx
from tests.unit.test_scene_canon import cel_book as _cel

pytestmark = pytest.mark.unit
# baseline.yml runs it in psql after pg_restore and before boot.
RESTORE_CANCEL = Path(arms.__file__).with_name("restore_cancel.sql").read_text()
cel_book = _cel  # the canon fixture, shared
BEATS = [
    {"description": "They meet.", "characters": ["Milo Voss", "Zara Okafor"]},
    {"description": "Zara leaves.", "characters": ["Zara Okafor"]},
]
CAST = re.compile(r"<character>.*?</character>", re.S)


class Store(FakeStore):  # s1 paragraph 1 is under the speech minimum
    async def list_chunks(self, character_id, chunk_type=None):
        rows = zip("s2 s1 s1 s1 s2 s2".split(), (3, 1, 2, 3, 1, 2), (4, 2, 4, 4, 4, 4))
        return [
            {"id": i, "source_id": src, "source": f"dialogue_{p}", "text": f"w{i} " * n}
            for src, p, n in rows
            for i in [f"{character_id[:4]}-{src}-{p}"]
        ]


class Recorder(FakeLLM):
    def __init__(self):
        super().__init__("[]")
        self.calls: list = []

    async def generate(self, messages, **kw):
        self.calls.append((messages[0]["content"], kw))
        return await super().generate(messages, **kw)


async def test_arm_a_differs_from_b_only_in_cast_context(
    async_session, cel_book, monkeypatch
):
    from app.orchestration import prose

    llm = Recorder()
    _bind(monkeypatch, async_session, llm)
    monkeypatch.setattr(arms, "get_async_session", lambda: _Ctx(async_session))
    for mod in ("app.characters.context", "evals.baseline.arms"):
        monkeypatch.setattr(f"{mod}.get_chunk_store", lambda: Store())
    ids = [str(cel_book.milo.id), str(cel_book.zara.id)]
    request = dict(
        character_ids=ids, characters=[], scene_description="Milo meets Zara."
    )
    request |= dict(setting="Desk", emotional_tone="wry", target_word_count=300)
    b = Scene(user_id=cel_book.book.user_id, chapter_id=cel_book.chapter.id, position=0)
    b.status = "processing"
    async_session.add(b)
    await async_session.commit()
    summary, position = await prose.scene_chapter(async_session, b)  # as the handler
    await prose.run_prose_scene_workflow(
        b.id,
        request,
        b.user_id,
        chapter_summary=summary,
        story_position=position,
        book_id=cel_book.book.id,
        checkpoint={"beats": BEATS, "prose": []},
    )
    job = Job(kind="generate_prose_scene", payload={"request": request})
    job.user_id, job.domain_type, job.domain_id = b.user_id, "scene", b.id
    async_session.add(job)
    b.deleted_at = b.created_at  # the step trashes B before A takes its position
    await async_session.commit()
    b_calls, llm.calls = llm.calls, []

    a_id, a_job, removed = await arms.run_arm_a(b.id)

    assert [kw["purpose"] for _p, kw in llm.calls] == ["beat_prose"] * 2  # seeded
    for (pa, ka), (pb, kb) in zip(llm.calls, b_calls, strict=True):
        assert ka == kb and CAST.sub("", pa) == CAST.sub("", pb)  # only the cast
    a_cast = "".join(CAST.findall(llm.calls[1][0]))  # beat 2: Zara alone
    z = str(cel_book.zara.id)[:4]
    assert f"w{z}-s1-2" in a_cast and "Milo" not in a_cast
    assert "hurried" in b_calls[1][0] and "hurried" not in a_cast  # A is A's
    for j, c, t in [(a_job, 0.5, 10), (a_job, None, 7), (uuid.uuid4(), 9, 1)]:
        async_session.add(APIUsage(job_id=j, cost_usd=c, tokens_used=t))
    await async_session.commit()
    rec = await arms.scene_record("cel-1", "A", a_id, a_job, removed)
    got = [rec[k] for k in ("cost_usd", "tokens", "unpriced_tokens", "requests")]
    assert got == [0.5, 17, 7, 2]
    first_a = await async_session.get(Scene, a_id)  # trashed, as a resume's orphan
    first_a.deleted_at = first_a.created_at
    await async_session.commit()
    failed = AsyncMock(return_value={"status": "failed", "error": "unusable"})
    monkeypatch.setattr(prose, "run_prose_scene_workflow", failed)
    with pytest.raises(arms.EvalClientError, match="arm A failed"):  # Codex P2
        await arms.run_arm_a(b.id)  # never memoized as a done slot


async def test_first_lines_are_eligible_in_source_order_and_count_the_later_cut(
    monkeypatch,
):
    monkeypatch.setattr(arms, "get_chunk_store", lambda: Store())
    scope = SceneScope((), (), (), frozenset(), "", (), {"s1": 3}, (), {})
    removed: set = set()
    lines = await arms.first_lines(type("C", (), {"id": "ch-x"}), scope, removed)
    # s1 1 is short and s1 3 later plot; the cap stops before s2 3.
    assert [s["id"] for s in lines] == ["ch-x-s1-2", "ch-x-s2-1", "ch-x-s2-2"]
    assert removed == {"ch-x-s1-3"}


def test_blinding_leaks_no_arm_string_and_not_the_sealed_key():
    marks = {"A": "```\n[[eval-arm-A]]", "B": "Beat 1: → ", "C": "**Beat 2.** "}
    records = [
        dict(slot=f"cel-{s}", arm=a, scene_id=f"scene-{s}{a}", job_id=f"job-{s}{a}")
        | {"text": f"{marks[a]}Prose of slot {s}."}
        for s in (1, 2)
        for a in "ABC"
    ]
    bundle, key = blind.blind(records, seed=987654321)
    shown = json.dumps(bundle) + blind.sheet(bundle)
    for leak in "987654321 cel- scene- job- eval- → ``` Beat arm".split():
        assert leak not in shown, leak
    assert set(bundle["items"].values()) == {"Prose of slot 1.", "Prose of slot 2."}
    assert len(bundle["pairs"]) == 6 and len(bundle["edit"]) == 2
    first = bundle["pairs"][0]
    assert blind.unblind([(first, None)], key)[0][0] == key["items"][first[0]][0]


def test_win_rates_with_cluster_ci_on_a_known_ordering():
    # 18 slots: B beats A in every judgment; C and A trade one win each, plus a tie.
    j = [(s, "A", "B", "B") for s in range(18) for _ in range(3)]
    j += [(s, "C", "A", w) for s in range(18) for w in ("C", "A", None)]
    out = score.score([], j)
    assert out["P(B>A)"] == {"p": 1.0, "ci": [1.0, 1.0], "slots": 18}
    assert out["P(C>A)"]["p"] == 0.5 and out["P(C>A)"]["ci"] == [0.5, 0.5]
    assert out["decision"] == "P(B>A) excludes 0.5"
    assert out["P(C>B) descriptive"]["slots"] == 0


@pytest.mark.parametrize(  # Codex P2 x2: extraction failed; indexing timed out
    "voice", [{"error": "extraction failed"}, {"indexed": False, "chunks": {}}]
)
async def test_an_ungrounded_prep_is_refused_before_the_outline(monkeypatch, voice):
    book = {"id": "b", "voice": voice}
    monkeypatch.setattr(arms, "_book_with_cast", AsyncMock(return_value=book))
    monkeypatch.setattr(arms, "_outline_into", outline := AsyncMock())
    with pytest.raises(arms.EvalClientError, match="voice preparation"):
        await arms.prepare(SimpleNamespace(book="cel"))
    outline.assert_not_awaited()


async def test_restore_cancel_then_entry_trashes_orphans_and_resumes(
    async_session, cel_book, monkeypatch, tmp_path
):
    ch, uid = cel_book.chapter, cel_book.book.user_id
    orphan = Scene(user_id=uid, chapter_id=ch.id, book_id=cel_book.book.id, position=1)
    async_session.add(orphan)
    await async_session.flush()
    job = Job(user_id=uid, kind="generate_prose_scene", status="running", payload={})
    job.domain_id, job.error = orphan.id, "daily request limit reached"
    async_session.add(job)
    await async_session.commit()
    await async_session.execute(text(RESTORE_CANCEL))  # the worker never claims it
    assert await claim_one(async_session, worker_id="w") is None
    assert (
        await async_session.get(Job, job.id, populate_existing=True)
    ).status == "cancelled"
    failed = {"id": str(orphan.id), "status": "failed", "mode_used": "prose"}
    live = {"scenes": [{"id": str(orphan.id)}]}
    client = SimpleNamespace(get_book=AsyncMock(), delete_scene=AsyncMock())
    client.get_chapter = AsyncMock(return_value=live)
    client.generate_scene = AsyncMock(return_value=failed)
    monkeypatch.setattr(arms, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setenv("EVAL_BASELINE_STATE", str(tmp_path / "state"))
    cache = Cache(str(tmp_path / "c"), "sha")
    slots = [{"chapter_id": "c", "brief": {"planned_beat_id": k}} for k in "123456"]
    cache.put("baseline-prep", "cel", {"book_id": "b", "slots": slots})
    for k in (1, 2):
        cache.put("baseline-slot", f"cel|{k}", [{"slot": f"cel-{k}", "arm": "B"}])
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "m")
    cache.put("baseline-gen", "cel", "gemini/m")  # the generator the slots used
    done = sorted(p.read_bytes() for p in (tmp_path / "c").rglob("*.json"))
    ctx = SimpleNamespace(book="cel", cache=cache, client=client)
    with pytest.raises(arms.EvalClientError, match="B failed: daily request limit"):
        await arms.step_baseline(ctx)
    client.delete_scene.assert_awaited_with(str(orphan.id))  # the orphan, trashed
    assert client.generate_scene.await_args[0][1]["planned_beat_id"] == "3"
    assert sorted(p.read_bytes() for p in (tmp_path / "c").rglob("*.json")) == done
    monkeypatch.setenv("LLM_MODEL", "other")  # Codex P2: a resume never mixes
    with pytest.raises(arms.EvalClientError, match="made by gemini/m"):
        await arms.step_baseline(ctx)
