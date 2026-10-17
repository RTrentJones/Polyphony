"""B1 round trip (SPEC-28): the app's real prompts, graded by the eval grader.

A capturing fake LLM records every `(messages, purpose)` the app sends and
answers with the stub LLM (`evals.stub_llm._answer`), which also proves the
stub parses the real prompts. The prompts are built through public entry
points only, for a cel-like book: the staged outline, the beat planner, a
whole planned scene (so `resolve_pov`, `scene_chapter` and the first and
last beat prompts run), a beat revise of it and the extraction reduce. Written as an archive, every prompt_contract
check must run and pass.

The store also holds four cel movement paragraphs (the intake, the drawer,
Zara's glance and her look), and the outline and the book plan put W4's "The
Locked Drawer" in chapter 2 and "Across the Partition" in chapter 1, so
`samples_not_plot` grades real samples (B-3) and the movement rule lists Zara
(D3a). Every call records its `response_format` (json_mode_where_json).

A failure here is a contract break between the app's prompts and the grader:
fix the prompt (or the marker), never soften the check.
"""

import json
import types
from pathlib import Path
from uuid import uuid4

import pytest

import app.llm.client as llm_client
from app.core.orm_models import Chapter, Character, Scene, Source, StyleGuide
from app.core.prompt_markers import ACT_CHAPTERS_HEADER
from evals import stub_llm
from evals.graders import plot
from evals.graders import prompt_contract as pc
from evals.steps import pipeline
from tests.unit.factories import add_chapter, add_scene, add_scene_beats

pytestmark = pytest.mark.unit
TIGHTEN = pipeline.TIGHTEN_INSTRUCTION


class CapturingLLM:
    """Records each call as a relay-archive record; answers like the stub."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def generate(self, messages, *, purpose="", **kw):
        prompt = "\n\n".join(str(m.get("content") or "") for m in messages)
        answer = _plan_the_drawer(prompt, stub_llm._answer(prompt))
        self.records.append(
            {
                "id": f"{len(self.records):04d}",
                "purpose": purpose,
                "created": float(len(self.records)),
                "messages": messages,
                "response_format": kw.get("response_format"),
                "answer": answer,
            }
        )
        return types.SimpleNamespace(text=answer, tokens_in=1, tokens_out=1)


_W4 = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "w4_outlines.json").read_text()
)["w4"]
_DRAWER, _PARTITION = (
    next({k: n[k] for k in ("title", "summary")} for n in _W4 if n["title"] == t)
    for t in ("The Locked Drawer", "Across the Partition")
)


def _plan_the_drawer(prompt: str, answer: str) -> str:
    """Act 1's second chapter also plans W4's "The Locked Drawer", and its
    first, last, "Across the Partition" (the gold's `partition`, chapter 1)."""
    if ACT_CHAPTERS_HEADER.format(number=1) not in prompt:
        return answer
    chapters = json.loads(answer)
    chapters[1]["children"].append({**_DRAWER, "people": ["Milo Voss"], "children": []})
    people = ["Milo Voss", "Zara Okafor"]
    chapters[0]["children"].append({**_PARTITION, "people": people, "children": []})
    return json.dumps(chapters)


class _Session:
    """`get_async_session` on the test session: commits on a clean exit."""

    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, exc_type, *a):
        if exc_type is None:
            await self._s.commit()
        return False


class _GoldStore:
    """Retrieval answering with each character's own corpus lines and, as
    `action` rows of the cel source, the excerpt paragraphs `moves` gives them."""

    def __init__(self, lines_by_id: dict[str, list[str]], moves=None, source_id=""):
        self.lines, self.moves, self.source_id = lines_by_id, moves or {}, source_id
        self.paragraphs = pipeline.load_excerpts("cel").split("\n\n")

    async def retrieve_similar(
        self, *, character_id, k=5, chunk_type=None, keep=None, **kw
    ):
        words = [
            {"id": f"{character_id}-{i}", "text": t, "score": 1.0 - i / 10}
            | {"chunk_type": "dialogue"}
            for i, t in enumerate(self.lines.get(character_id, [])[:k])
        ]
        moves = [
            {"id": f"{character_id}-p{n}", "text": self.paragraphs[n], "score": 0.5}
            | {"chunk_type": "action", "source_id": self.source_id}
            | {"source": f"paragraph_{n}"}
            for n in self.moves.get(character_id, [])[:k]
        ]
        rows = {"dialogue": words, "action": moves, None: words + moves}
        return [r for r in rows.get(chunk_type, []) if keep is None or keep(r)]


async def _cel_book(session, book):
    """The cel book: its canon, the excerpt as a live source (so the secondaries
    have lines in the sources) and a second live chapter at position 1 (so the
    scene's chapter, added at position 0, is "1 of 2")."""
    _, gt = pipeline.load_corpus("cel")
    style = pipeline.load_style("cel")
    book.synopsis = gt["synopsis"]
    cast = []
    for payload in pipeline._cast_with_roles(gt):
        cast.append(Character(book_id=book.id, user_id=book.user_id, **payload))
    session.add_all(cast)
    excerpt = pipeline.load_excerpts("cel")
    source = Source(
        book_id=book.id,
        user_id=book.user_id,
        title="cel excerpts",
        content_text=excerpt,
        word_count=len(excerpt.split()),
        status="completed",
    )
    second = Chapter(
        book_id=book.id,
        title="The second",
        position=1,
        summary="",
        planned_beats=[_DRAWER],
    )
    session.add_all([source, second])
    session.add(
        StyleGuide(
            book_id=book.id,
            **{k: v for k, v in style.items() if k[0] != "_"},
        )
    )
    await session.commit()
    return gt, style, {c.name: c for c in cast}, str(source.id)


async def test_the_apps_prompts_pass_every_contract_check(
    async_session, test_book, monkeypatch, tmp_path
):
    import app.characters.context as ctx_mod
    import app.jobs.revise as revise_job
    import app.orchestration.prose as prose_mod
    import app.orchestration.revise as rv
    from app.orchestration.pov import resolve_pov
    from app.orchestration.workflow import StoryPosition, plan_scene_beats
    from app.parsing.canon_extractor import extract_canon
    from app.planning.canon import build_canon
    from app.planning.planned_beat import beat_scene_description
    from app.planning.staged_outline import generate_staged_outline

    gt, style, cast, source_id = await _cel_book(async_session, test_book)
    fake = CapturingLLM()
    monkeypatch.setattr(llm_client, "_client", fake)
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Session(async_session))
    gold = {
        str(c.id): gt["gold_lines"][n]["train"]
        for n, c in cast.items()
        if n in gt["gold_lines"]
    }
    # The intake and the drawer are Milo's movement; the look at the column and
    # the partition glance (paragraph 3, the PM's Q2 ruling: plot) Zara's.
    moves = {str(cast["Milo Voss"].id): [0, 13], str(cast["Zara Okafor"].id): [29, 3]}
    store = _GoldStore(gold, moves, source_id)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: store)

    # 1. The staged outline: skeleton, chapters and beats.
    canon = await build_canon(async_session, test_book.id)
    nodes, _ = await generate_staged_outline(canon, chapters_target=6)
    # beats_see_whole_outline bites only when another act's chapters exist.
    assert len({n["act_index"] for n in nodes}) >= 2
    names = {str(c.id): n for n, c in cast.items()}
    chapter = next(n for n in nodes if n.get("children"))
    beat = chapter["children"][0]
    beat_cast = [names[i] for i in beat.get("character_ids") or [] if i in names]
    assert beat_cast, beat

    # 2. The beat planner, for a planned beat with its setting, tone and
    # named secondaries (the synopsis' Prava).
    request = {
        "scene_description": beat_scene_description(beat),
        "characters": beat_cast,
        "setting": beat.get("setting") or "",
        "emotional_tone": beat.get("tone") or "",
        "secondary_names": beat.get("secondary_names") or [],
        "planned_beat_id": beat["node_id"],
    }
    assert request["secondary_names"], beat
    await plan_scene_beats(
        request,
        canon_block=canon.full_block(),
        chapter_summary=chapter["summary"],
        pov=resolve_pov(request, canon),
        story_position=StoryPosition(1, 2),
        secondary_block="",
        plan=[(1, b) for b in chapter["children"]],
        earlier=[],
    )

    # 3. A whole planned scene: the POV comes from the style guide, the
    # position from the chapter rows (B-4: computed, not typed in). It is the
    # chapter's second planned scene; the first is written (plan_sees_chapter).
    first = {"node_id": str(uuid4()), "title": "Before", "summary": "Milo waits."}
    row = await add_chapter(
        async_session,
        test_book,
        title=chapter["title"],
        summary=chapter["summary"],
        planned_beats=[first, *chapter["children"]],
    )
    written = await add_scene(
        async_session, test_book, row, beat=first["node_id"], title="Before"
    )
    await add_scene_beats(async_session, written, ["Milo waits in the atrium."])
    await async_session.commit()
    assert row.number == 1
    scene_request = {
        **request,
        "characters": [],
        "character_ids": [str(cast[n].id) for n in beat_cast],
        "target_word_count": 300,
    }
    scene = Scene(
        user_id=test_book.user_id,
        chapter_id=row.id,
        position=1,
        status="processing",
        scene_request=scene_request,
    )
    async_session.add(scene)
    await async_session.commit()
    summary, position = await prose_mod.scene_chapter(async_session, scene)
    assert (summary, position) == (chapter["summary"], StoryPosition(1, 2))
    result = await prose_mod.run_prose_scene_workflow(
        scene.id,
        scene_request,
        test_book.user_id,
        chapter_summary=summary,
        story_position=position,
        book_id=test_book.id,
    )
    assert result["status"] == "completed", result
    assert sum(r["purpose"] == "beat_prose" for r in fake.records) >= 2

    # 3b. Redo beat 2 of that scene (the revise job, as the worker runs it).
    monkeypatch.setattr(
        revise_job, "get_async_session", lambda: _Session(async_session)
    )
    await async_session.refresh(scene)
    span = await rv.resolve_scene_span(async_session, scene, rv.SpanSpec(beat_index=1))
    await revise_job.run(
        {
            "scene_id": str(scene.id),
            "book_id": str(test_book.id),
            "user_id": str(test_book.user_id),
            "base_text_hash": rv.text_hash(scene.draft),
            "instruction": "Redo beat 2.",
            "range": [span.start, span.end],
            "beat_index": 1,
        }
    )
    assert fake.records[-1]["purpose"] == "scene_revise"
    # 3c. A tighten of the scene's first paragraph (W8-1: tighten_clause).
    await async_session.refresh(scene)
    para = scene.draft.split("\n\n")[0]
    await revise_job.run(
        {
            "scene_id": str(scene.id),
            "book_id": str(test_book.id),
            "user_id": str(test_book.user_id),
            "base_text_hash": rv.text_hash(scene.draft),
            "instruction": TIGHTEN,
            "range": [0, len(para)],
        }
    )
    assert fake.records[-1]["purpose"] == "scene_revise"

    # 4. Extraction over the cel excerpt, for the reduce prompt.
    await extract_canon(pipeline.load_corpus("cel")[0])

    # 5. The voice probe, on the scene path's samples (SPEC-17, W3-5).
    from app.characters.context import SceneScope
    from app.characters.dialogue import generate_dialogue

    milo = cast["Milo Voss"]
    await generate_dialogue(
        character=milo,
        prompt=gt["canonical_beats"][0]["title"],
        context="",
        scope=SceneScope.of(canon, [milo], (), (), {}),
    )
    assert any(r["purpose"] == "dialogue" for r in fake.records)

    for rec in fake.records:
        (tmp_path / f"{rec['id']}.json").write_text(json.dumps(rec))
    paragraphs = store.paragraphs
    held = {
        n: [paragraphs[i] for i in moves[str(cast[n].id)]]
        for n in cast
        if str(cast[n].id) in moves
    }
    records = pc.load_archive(tmp_path)
    grade = lambda: pc.grade(  # noqa: E731
        records,
        # This run confirmed no premise: the skeleton's own is the one to carry.
        **{**pc.corpus_inputs(gt, style), "premise_edit": None},
        movement_store=held,
        revise_rows=[{"id": "tighten", "kind": "tighten", "instruction": TIGHTEN}],
    )
    # W7-2 (S-4): unsigned, the plot values are null and the run says so.
    monkeypatch.setattr(plot, "PLACEMENTS", tmp_path / "placements")
    plot.PLACEMENTS.mkdir()
    out = grade()
    assert (out["placement_signed"], out["samples_not_plot"], out["movement"]) == (
        False,
        None,
        None,
    )
    # Signed as the draft reads it (the stub's outline plans no excerpt event
    # by other words), every check runs.
    cells = plot.draft_placements(gt["excerpt_events"], plot.outline_chapters(records))
    signed = {"outline_hash": plot.outline_hash(records), "signed_by": "PM"}
    signed |= {"run": "roundtrip", "signed_at": "2026-10-16", "placements": cells}
    (plot.PLACEMENTS / "roundtrip.json").write_text(json.dumps(signed))
    out = grade()
    assert out["placement_signed"] and out["evidence_verbatim"]["lines"]["n"] > 0
    ran = {c: out[c] for c in pc.CHECKS if out[c]["n"]}
    failing = {c: r["failures"] for c, r in ran.items() if r["rate"] != 1.0}
    # B-3: the gold sees real samples, and none of them is plot.
    assert out["samples_not_plot"]["classified"] > 0
    assert out["samples_not_plot"]["later_plot_slots"] == 0
    assert out["voice_dialogue"]["voice_coverage"] >= 0.8, out["voice_dialogue"]
    assert not failing, json.dumps(failing, indent=1)[:4000]
    assert set(ran) == set(pc.CHECKS)  # 24, each n > 0, json_mode_where_json strict
    assert out["plan_cast_from_beat"]["unmatched"] == []
    assert out["voice_dialogue"]["short_samples"] == 0
    assert out["sample_lift"]["max_shared"] == 0
    # D3a: Milo's intake is eligible (the stub plans no intake) and moves; Zara
    # is listed with the partition glance (3) and the look past the tail (29).
    move = out["movement"]
    assert move["n"] and move["passed"] == move["n"]
    assert move["ineligible"]["Zara Okafor"] == [3, 29]
    # D4 (wave 8): Zara (only plot lines stored) and Edric (none stored) are
    # cast in chapter 1 and never eligible, so the gate reads false, naming them.
    assert (move["ok"], move["not_eligible"]) == (False, ["Edric Thane", "Zara Okafor"])
