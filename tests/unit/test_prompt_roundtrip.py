"""B1 round trip (SPEC-28): the app's real prompts, graded by the eval grader.

A capturing fake LLM records every `(messages, purpose)` the app sends and
answers with the stub LLM (`evals.stub_llm._answer`), which also proves the
stub parses the real prompts. The prompts are built through public entry
points only, for a cel-like book: the staged outline, the beat planner, a
whole planned scene (so `resolve_pov` and the first and last beat prompts
run) and the extraction reduce. Written as an archive, every prompt_contract
check that ran must pass.

A failure here is a contract break between the app's prompts and the grader:
fix the prompt (or the marker), never soften the check.
"""

import json
import types

import pytest

import app.llm.client as llm_client
from app.core.orm_models import Chapter, Character, Scene, StyleGuide
from evals import stub_llm
from evals.graders import prompt_contract as pc
from evals.steps import pipeline

pytestmark = pytest.mark.unit

MUST_RUN = (
    "beats_block",
    "prior_paragraphs",
    "beats_stage",
    "outline_no_repr",
    "plan_cast_from_beat",
    "no_blank_setting",
    "no_neutral_tone",
    "pov_line_has_mode_and_character",
    "last_beat_no_wrap_up",
    "no_duplicate_bible",
    "no_doubled_voice_label",
    "reduce_evidence_depth",
)


class CapturingLLM:
    """Records each call as a relay-archive record; answers like the stub."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    async def generate(self, messages, *, purpose="", **kw):
        prompt = "\n\n".join(str(m.get("content") or "") for m in messages)
        answer = stub_llm._answer(prompt)
        self.records.append(
            {
                "id": f"{len(self.records):04d}",
                "purpose": purpose,
                "created": float(len(self.records)),
                "messages": messages,
                "answer": answer,
            }
        )
        return types.SimpleNamespace(text=answer, tokens_in=1, tokens_out=1)


class _Session:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class _GoldStore:
    """Voice retrieval answering with each character's own corpus lines."""

    def __init__(self, lines_by_id: dict[str, list[str]]):
        self.lines = lines_by_id

    async def retrieve_similar(self, *, character_id, k=5, **kw):
        return [
            {"id": f"{character_id}-{i}", "text": t, "score": 1.0 - i / 10}
            | {"chunk_type": "dialogue"}
            for i, t in enumerate(self.lines.get(character_id, [])[:k])
        ]


async def _cel_book(session, book):
    _, gt = pipeline.load_corpus("cel")
    style = pipeline.load_style("cel")
    book.synopsis = gt["synopsis"]
    cast = []
    for payload in pipeline._cast_with_roles(gt):
        cast.append(Character(book_id=book.id, user_id=book.user_id, **payload))
    session.add_all(cast)
    session.add(
        StyleGuide(
            book_id=book.id,
            **{k: v for k, v in style.items() if k[0] != "_"},
        )
    )
    await session.commit()
    return gt, style, {c.name: c for c in cast}


async def test_the_apps_prompts_pass_every_contract_check(
    async_session, test_book, monkeypatch, tmp_path
):
    import app.characters.context as ctx_mod
    import app.orchestration.prose as prose_mod
    from app.orchestration.workflow import plan_scene_beats
    from app.parsing.canon_extractor import extract_canon
    from app.planning.canon import build_canon
    from app.planning.planned_beat import beat_scene_description
    from app.planning.staged_outline import generate_staged_outline

    gt, style, cast = await _cel_book(async_session, test_book)
    fake = CapturingLLM()
    monkeypatch.setattr(llm_client, "_client", fake)
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Session(async_session))
    gold = {
        str(c.id): gt["gold_lines"][n]["train"]
        for n, c in cast.items()
        if n in gt["gold_lines"]
    }
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: _GoldStore(gold))

    # 1. The staged outline: skeleton, chapters and beats.
    canon = await build_canon(async_session, test_book.id)
    nodes, _ = await generate_staged_outline(canon, chapters_target=6)
    names = {str(c.id): n for n, c in cast.items()}
    chapter = next(n for n in nodes if n.get("children"))
    beat = chapter["children"][0]
    beat_cast = [names[i] for i in beat.get("character_ids") or [] if i in names]
    assert beat_cast, beat

    # 2. The beat planner, for a planned beat with its setting and tone.
    request = {
        "scene_description": beat_scene_description(beat),
        "characters": beat_cast,
        "setting": beat.get("setting") or "",
        "emotional_tone": beat.get("tone") or "",
    }
    await plan_scene_beats(
        request, canon_block=canon.full_block(), chapter_summary=chapter["summary"]
    )

    # 3. A whole planned scene: the POV comes from the style guide.
    row = Chapter(book_id=test_book.id, title=chapter["title"], position=0, summary="")
    async_session.add(row)
    await async_session.commit()
    scene = Scene(
        user_id=test_book.user_id, chapter_id=row.id, position=0, status="processing"
    )
    async_session.add(scene)
    await async_session.commit()
    result = await prose_mod.run_prose_scene_workflow(
        scene.id,
        {
            **request,
            "characters": [],
            "character_ids": [str(cast[n].id) for n in beat_cast],
            "target_word_count": 300,
        },
        test_book.user_id,
        chapter_summary=chapter["summary"],
        book_id=test_book.id,
    )
    assert result["status"] == "completed", result
    assert sum(r["purpose"] == "beat_prose" for r in fake.records) >= 2

    # 4. Extraction over the cel excerpt, for the reduce prompt.
    await extract_canon(pipeline.load_corpus("cel")[0])

    for rec in fake.records:
        (tmp_path / f"{rec['id']}.json").write_text(json.dumps(rec))
    out = pc.grade(
        pc.load_archive(tmp_path),
        # This run confirmed no premise: the skeleton's own is the one to carry.
        **{**pc.corpus_inputs(gt, style), "premise_edit": None},
    )
    ran = {c: out[c] for c in pc.CHECKS if out[c]["n"]}
    failing = {c: r["failures"] for c, r in ran.items() if r["rate"] != 1.0}
    assert not failing, json.dumps(failing, indent=1)[:4000]
    assert [c for c in MUST_RUN if c not in ran] == []
    assert out["plan_cast_from_beat"]["unmatched"] == []
