"""The scene beat planner (SPEC-24 POV, SPEC-30 story position)."""

import json
import types
from pathlib import Path

import pytest

import app.orchestration.workflow as wf
from app.core.prompt_markers import CHAPTER_SCENES_HEADER, EARLIER_BEATS, POV_LINE_RE
from app.orchestration.pov import Pov
from app.orchestration.workflow import StoryPosition, plan_scene_beats

pytestmark = pytest.mark.unit

_REQUEST = {"scene_description": "Intake.", "characters": ["Milo Voss", "Zara"]}


async def _plan(monkeypatch, pov, answer="[]", request=_REQUEST, plan=(), earlier=()):
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
        request,
        canon_block="",
        chapter_summary="Milo's first day.",
        pov=pov,
        story_position=StoryPosition(1, 5),
        secondary_block="",
        plan=list(plan),
        earlier=list(earlier),
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


# W4b2's plan, and record 0015's last beat line for "Strong Culture Fit".
_W4B2 = json.loads(
    (Path(__file__).parents[1] / "fixtures/w4_outlines.json").read_text()
)
_PLAN = [(b["chapter"], {**b, "node_id": f"n{i}"}) for i, b in enumerate(_W4B2["w4b2"])]
_FIT = "Prava stamps his file, hands him a lanyard, and says his name once more."
_FIRST = Pov("first person")
_KEEP = "Keep the days, times and objects these establish"


async def test_the_planner_sees_its_chapter_and_what_earlier_scenes_planned(
    monkeypatch,
):
    """(F) W5-4 (a): "Lanyard, Desk, Mentor" with chapter 1's first two written."""
    earlier = [("Mid-Sentence", ["Milo dies."]), ("Strong Culture Fit", ["Hi.", _FIT])]
    ask = {**_REQUEST, "planned_beat_id": "n2"}
    prompt = (await _plan(monkeypatch, _FIRST, "[]", ask, _PLAN, earlier))[0]
    block = prompt.split(f"{CHAPTER_SCENES_HEADER}\n")[1].split("\n\n")[0].splitlines()
    assert [ln[:16] for ln in block] == [
        "  1. Mid-Sentenc", "  2. Strong Cult", "→ 3. Lanyard, De",
        EARLIER_BEATS.format(what="the earlier scenes of this chapter")[:16],
        "- Mid-Sentence: ", "- Strong Culture", _KEEP[:16],
    ]  # fmt: skip
    assert block[-2] == f"- Strong Culture Fit: Hi. {_FIT}"
    at = prompt.index
    assert at("chapter 1 of 5") < at(block[0]) < at("<scene>")
    ask["planned_beat_id"] = "n0"
    prompt = (await _plan(monkeypatch, _FIRST, "[]", ask, _PLAN))[0]
    assert f"{CHAPTER_SCENES_HEADER}\n→ 1. Mid-Sentence: " in prompt
    assert "Beats already planned" not in prompt and _KEEP not in prompt


async def test_the_also_named_sentence_needs_a_list(monkeypatch):
    """(F) W5-4 (b): absent without a list (W4b2 0009, 0020); (R) kept with one."""
    assert "Also named" not in (await _plan(monkeypatch, _FIRST))[0]
    ask = {**_REQUEST, "secondary_names": ["Prava"]}
    prompt = (await _plan(monkeypatch, _FIRST, "[]", ask))[0]
    assert 'A person listed as "Also named"' in prompt
