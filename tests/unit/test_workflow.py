"""The scene beat planner (SPEC-24 POV, SPEC-30 story position)."""

import types

import pytest

import app.orchestration.workflow as wf
from app.core.prompt_markers import POV_LINE_RE
from app.orchestration.pov import Pov
from app.orchestration.workflow import StoryPosition, plan_scene_beats

pytestmark = pytest.mark.unit

_REQUEST = {"scene_description": "Intake.", "characters": ["Milo Voss", "Zara"]}


async def _plan(monkeypatch, pov, answer="[]"):
    prompts, events = [], []

    class Fake:
        async def generate(self, messages, **kw):
            prompts.append(messages[0]["content"])
            return types.SimpleNamespace(text=answer, tokens_in=1, tokens_out=1)

    monkeypatch.setattr(wf, "get_llm_client", lambda: Fake())
    monkeypatch.setattr(
        wf.logger, "warning", lambda msg, extra_fields: events.append(extra_fields)
    )
    beats = await plan_scene_beats(
        _REQUEST,
        canon_block="",
        chapter_summary="Milo's first day.",
        pov=pov,
        story_position=StoryPosition(2, 5),
        secondary_block="",
    )
    return prompts[0], beats, events


async def test_a_limited_plan_stays_in_the_pov_head_and_knows_its_place(monkeypatch):
    prompt, _, _ = await _plan(monkeypatch, Pov("third person limited", "Milo Voss"))
    assert POV_LINE_RE.search(prompt).group("who") == "Milo Voss"
    assert "plan the moment Milo Voss learns the name" in prompt


async def test_an_omniscient_plan_is_not_held_to_one_head(monkeypatch):
    prompt, _, _ = await _plan(monkeypatch, Pov("third person omniscient"))
    assert POV_LINE_RE.search(prompt)
    assert "Plan only what" not in prompt


async def test_a_non_json_answer_is_one_whole_scene_beat(monkeypatch):
    _, beats, events = await _plan(monkeypatch, Pov("first person"), "1. a\n2. b")
    assert beats == [
        {"description": "Intake.", "characters": ["Milo Voss", "Zara"], "dialogue": []}
    ]
    assert [e["event"] for e in events] == ["plan_beats_unparsed"]
