"""W12-5 (SPEC-84): the baseline harness — arm A through the product workflow,
only context assembly differing, cost by job id, blinding, and the statistic."""

import json
import uuid

import pytest

from app.characters.context import SceneScope
from app.core.orm_models import APIUsage, Job, Scene
from evals.baseline import arms, blind, score
from tests.unit.test_scene_canon import FakeLLM, FakeStore, _bind, _Ctx
from tests.unit.test_scene_canon import cel_book as _cel

pytestmark = pytest.mark.unit
cel_book = _cel  # the canon fixture, shared
BEATS = [
    {"description": "They meet.", "characters": ["Milo Voss", "Zara Okafor"]},
    {"description": "Zara leaves.", "characters": ["Zara Okafor"]},
]


def _row(i, words, para, source="s1"):
    text = " ".join(f"w{i}" for _ in range(words))
    return {
        "id": i,
        "source_id": source,
        "chunk_type": "dialogue",
        "text": text,
        "source": f"dialogue_{para}",
    }


class Store(FakeStore):
    async def list_chunks(self, character_id, chunk_type=None):
        return [
            _row(f"{character_id[:4]}-{p}", 2 if p == 1 else 4, p)
            for p in (5, 1, 2, 3, 4)
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

    llm, built = Recorder(), {"A": [], "B": []}
    _bind(monkeypatch, async_session, llm)
    monkeypatch.setattr(arms, "get_async_session", lambda: _Ctx(async_session))
    for mod in ("app.characters.context", "evals.baseline.arms"):
        monkeypatch.setattr(f"{mod}.get_chunk_store", lambda: Store())

    def spy(arm, real):
        async def build(*a, **kw):
            built[arm].append(await real(*a, **kw))
            return built[arm][-1]

        return build

    monkeypatch.setattr(
        arms,
        "a_cast_builder",
        lambda removed, real=arms.a_cast_builder: spy("A", real(removed)),
    )
    ids = [str(cel_book.milo.id), str(cel_book.zara.id)]
    request = {
        "character_ids": ids,
        "characters": [],
        "scene_description": "Milo meets Zara.",
        "setting": "Desk",
        "emotional_tone": "wry",
        "target_word_count": 300,
    }
    b = Scene(
        user_id=cel_book.book.user_id,
        chapter_id=cel_book.chapter.id,
        position=0,
        status="processing",
    )
    async_session.add(b)
    await async_session.commit()
    summary, position = await prose.scene_chapter(async_session, b)  # as the handler
    seed, b_builder = {"beats": BEATS, "prose": []}, spy("B", prose.build_cast_context)
    await prose.run_prose_scene_workflow(
        b.id,
        request,
        b.user_id,
        chapter_summary=summary,
        story_position=position,
        book_id=cel_book.book.id,
        checkpoint=seed,
        cast_builder=b_builder,
    )
    async_session.add(
        Job(
            user_id=b.user_id,
            kind="generate_prose_scene",
            payload={"request": request},
            domain_type="scene",
            domain_id=b.id,
        )
    )
    b.deleted_at = b.created_at  # the step trashes B before A takes its position
    await async_session.commit()
    b_calls, llm.calls = llm.calls, []

    a_id, a_job, removed = await arms.run_arm_a(b.id)

    assert [c[1]["purpose"] for c in llm.calls] == [
        "beat_prose",
        "beat_prose",
    ]  # beats seeded
    for (pa, ka), (pb, kb), ca, cb in zip(llm.calls, b_calls, built["A"], built["B"]):
        assert ka == kb and pa.replace(ca, "") == pb.replace(cb, "")  # only context
    zara = [f"w{str(cel_book.zara.id)[:4]}-{p}" in built["A"][1] for p in range(1, 6)]
    assert zara == [False, True, True, True, False] and "Milo" not in built["A"][1]
    assert removed == 0
    async_session.add_all(
        [
            APIUsage(job_id=a_job, cost_usd=0.5, tokens_used=10),
            APIUsage(job_id=a_job, tokens_used=7),
            APIUsage(job_id=uuid.uuid4(), cost_usd=9, tokens_used=1),
        ]
    )
    await async_session.commit()
    rec = await arms.scene_record("cel-1", "A", a_id, a_job, removed)
    assert (
        rec["cost_usd"],
        rec["tokens"],
        rec["unpriced_tokens"],
        rec["later_plot_removed"],
    ) == (0.5, 17, 7, 0)


async def test_first_lines_are_eligible_in_source_order_and_count_the_later_cut(
    monkeypatch,
):
    monkeypatch.setattr(arms, "get_chunk_store", lambda: Store())
    scope = SceneScope((), (), (), frozenset(), "", (), {"s1": 3}, (), {})
    removed: set = set()
    lines = await arms.first_lines(type("C", (), {"id": "ch-x"}), scope, removed)
    # Paragraph 1 is under the speech minimum; 3, 4 and 5 are later plot.
    assert [s["source"] for s in lines] == ["dialogue_2"] and removed == {
        "ch-x-3",
        "ch-x-4",
        "ch-x-5",
    }


def test_blinding_leaks_no_arm_string_and_not_the_sealed_key():
    marks = {"A": "```\n[[eval-arm-A]]", "B": "Beat 1: → ", "C": "**Beat 2.** "}
    records = [
        {
            "slot": f"cel-{s}",
            "arm": arm,
            "scene_id": f"scene-{s}{arm}",
            "job_id": f"job-{s}{arm}",
            "text": f"{marks[arm]}Prose of slot {s}.",
        }
        for s in (1, 2)
        for arm in "ABC"
    ]
    bundle, key = blind.blind(records, seed=987654321)
    shown = json.dumps(bundle) + blind.sheet(bundle)
    for leak in [
        "987654321",
        "cel-",
        "scene-",
        "job-",
        "eval-",
        "→",
        "```",
        "Beat",
        "arm",
    ]:
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
    assert (
        out["decision"] == "P(B>A) excludes 0.5"
        and out["P(C>B) descriptive"]["slots"] == 0
    )
