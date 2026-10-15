"""SPEC-24 (W2-1 prompt half, W2-2): each outline beat carries its own cast,
setting, tone and named secondaries; the scene planner treats the cast as
available; blank settings and the "neutral" tone never reach a prompt.

The P1-P4 strings are the contract E6's prompt_contract grader greps for
(tpm-plan-wave2 §1.3), so they are pinned here verbatim."""

import json
import re
import types
import uuid

import pytest

from app.orchestration import workflow as wf
from app.orchestration.workflow import (
    parse_structured_beats,
    plan_scene_beats,
    scene_block,
)
from app.planning import staged_outline as so
from app.planning.canon import Canon
from app.planning.outline import validate_outline_nodes
from evals.steps.pipeline import load_corpus

pytestmark = pytest.mark.unit

P1 = re.compile(
    r"(?m)^SECONDARY PEOPLE \(named in the synopsis or canon, not in the cast; "
    r"you may name them, never invent others\): (.+)$"
)
P3_CAST = re.compile(r"(?m)^Available cast \(not all need appear\): (.+)$")
P3_ALSO = re.compile(r"(?m)^Also named \(secondary, not in the cast\): (.+)$")
BLANK_SETTING = re.compile(r"(?m)^\s*Setting:\s*$")


def _person(name, aliases=(), role="protagonist"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        aliases=list(aliases),
        role=role,
        description="",
        goals="",
        arc="",
        notes="",
        personality_traits=None,
        voice_characteristics=None,
        relationships=None,
    )


@pytest.fixture
def cel():
    _text, gt = load_corpus("cel")
    return Canon(
        title="Bored to Undeath",
        synopsis=gt["synopsis"],
        characters=[
            _person("Milo Voss", ["Milo"]),
            _person("Zara Okafor", ["Zara"], role="deuteragonist"),
            _person("Edric Thane", ["Edric", "the Lich"], role="antagonist"),
        ],
    )


class FakeLLM:
    def __init__(self, responses: dict):
        self.responses = responses
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
        return types.SimpleNamespace(
            text=self.responses.get(purpose, "[]"), tokens_in=1, tokens_out=1
        )


# --- P1 / P2: the outline prompts ------------------------------------------------


async def test_chapters_prompt_lists_secondaries_and_keeps_principals_rule(
    monkeypatch, cel
):
    fake = FakeLLM({"outline:chapters": "[]"})
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    await so._stage_chapters(cel, {"acts": [{"title": "A"}]}, None)
    prompt = fake.prompts["outline:chapters"][0]
    names = [n.strip() for n in P1.search(prompt).group(1).split(",")]
    assert "Prava" in names and "Old Fen" in names
    assert "Milo Voss" not in names
    assert "Every name you use for a character MUST be one of" not in prompt
    assert "Principal characters MUST be canon cast" in prompt
    assert '"secondary_names"' in prompt


async def test_beats_prompt_has_p1_and_the_p2_child_fields(monkeypatch, cel):
    fake = FakeLLM({"outline:beats": "[]"})
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    chapters = [{"title": "Intake", "act_index": 0, "characters": ["Milo Voss"]}]
    await so._stage_beats_for_act(cel, 0, chapters, None, skeleton={"acts": []})
    prompt = fake.prompts["outline:beats"][0]
    assert P1.search(prompt)
    assert "Every name you use for a character MUST be one of" not in prompt
    assert "Principal characters MUST be canon cast" in prompt
    children = prompt[prompt.index('"children": [{') :]
    for key in ('"characters"', '"secondary_names"', '"setting"', '"tone"'):
        assert key in children, key
    # the stub's markers and the grader's purpose marker survive
    assert "scene beats" in prompt and "mirroring the chapters" in prompt
    assert "write 2-4 scene beats" in prompt
    assert "act 1" in prompt  # acts still numbered from 1


async def test_p1_says_none_without_secondaries(monkeypatch):
    canon = Canon(
        title="T", synopsis="Milo Voss works.", characters=[_person("Milo Voss")]
    )
    fake = FakeLLM({})
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    await so._stage_chapters(canon, {"acts": []}, None)
    assert P1.search(fake.prompts["outline:chapters"][0]).group(1) == "(none)"


# --- §1.1: validating the beat children -------------------------------------------


def _cast():
    return [_person("Milo Voss", ["Milo"]), _person("Zara Okafor", ["Zara"])]


def test_children_resolve_cast_and_keep_setting_tone_secondaries():
    milo, zara = _cast()
    nodes = validate_outline_nodes(
        [
            {
                "title": "Ch",
                "characters": ["Milo"],
                "children": [
                    {
                        "title": "Intake",
                        "summary": "what changes",
                        "characters": ["Milo Voss", "Ghost"],
                        "secondary_names": [" Prava ", "Zara", "prava", ""],
                        "setting": "  Intake Atrium  ",
                        "tone": "bureaucratic dread",
                        "children": [],
                    },
                    {"title": "No cast given"},
                ],
            }
        ],
        cast=[milo, zara],
    )
    beat, bare = nodes[0]["children"]
    # names resolve to ids; a secondary that IS cast moves to character_ids;
    # an unknown name is dropped on the generation path
    assert beat["character_ids"] == [str(milo.id), str(zara.id)]
    assert beat["secondary_names"] == ["Prava"]
    assert beat["setting"] == "Intake Atrium"
    assert beat["tone"] == "bureaucratic dread"
    assert "character_ids" not in bare and "setting" not in bare
    # a restore (no cast) and a re-validation are byte-identical
    assert validate_outline_nodes(nodes) == nodes
    assert validate_outline_nodes(nodes, cast=[milo, zara]) == nodes


def test_strict_rejects_an_unknown_beat_name_unless_it_is_secondary():
    milo, zara = _cast()
    with pytest.raises(ValueError, match="Ghost"):
        validate_outline_nodes(
            [{"title": "Ch", "children": [{"title": "b", "characters": ["Ghost"]}]}],
            cast=[milo, zara],
            strict=True,
        )
    nodes = validate_outline_nodes(
        [
            {
                "title": "Ch",
                "children": [
                    {
                        "title": "b",
                        "characters": ["Milo", "Prava"],
                        "secondary_names": ["Prava"],
                    }
                ],
            }
        ],
        cast=[milo, zara],
        strict=True,
    )
    beat = nodes[0]["children"][0]
    assert beat["character_ids"] == [str(milo.id)]
    assert beat["secondary_names"] == ["Prava"]


def test_restore_keeps_stored_beat_ids_as_they_are():
    gone = str(uuid.uuid4())
    stored = [
        {
            "node_id": str(uuid.uuid4()),
            "title": "Ch",
            "summary": "",
            "children": [
                {
                    "node_id": str(uuid.uuid4()),
                    "title": "b",
                    "summary": "",
                    "children": [],
                    "character_ids": [gone],
                    "secondary_names": ["Prava"],
                    "setting": "",
                    "tone": "",
                }
            ],
        }
    ]
    assert validate_outline_nodes(stored) == stored


def test_secondary_names_are_capped_and_trimmed():
    names = [f"Person {i}" for i in range(20)] + ["x" * 300]
    nodes = validate_outline_nodes(
        [{"title": "Ch", "children": [{"title": "b", "secondary_names": names}]}]
    )
    out = nodes[0]["children"][0]["secondary_names"]
    assert len(out) == 12 and out[0] == "Person 0"
    assert all(len(n) <= 100 for n in out)


def test_top_level_secondaries_are_kept_and_exempt_from_strict():
    milo, zara = _cast()
    nodes = validate_outline_nodes(
        [{"title": "Ch", "characters": ["Prava"], "secondary_names": ["Prava"]}],
        cast=[milo, zara],
        strict=True,
    )
    assert nodes[0]["secondary_names"] == ["Prava"]
    assert nodes[0]["character_ids"] == []


# --- the generation path filters secondaries against the canon text -----------------


def test_generation_drops_invented_secondaries_and_keeps_canon_ones(cel):
    from app.planning.fidelity import canon_known_text
    from app.planning.outline import CastIndex

    nodes = so.keep_known_secondaries(
        [
            {
                "title": "Ch",
                "characters": ["Milo Voss"],
                "children": [
                    {
                        "title": "Intake",
                        "characters": ["Milo Voss", "Old Fen"],
                        "secondary_names": ["Prava", "Bartholomew Quince"],
                    }
                ],
            }
        ],
        canon_known_text(cel),
        CastIndex(cel.characters),
    )
    beat = nodes[0]["children"][0]
    assert beat["secondary_names"] == ["Prava", "Old Fen"]
    assert "secondary_names" not in nodes[0]


async def test_staged_outline_saves_beat_fields_and_warns_on_invention(
    monkeypatch, cel
):
    skeleton = {
        "premise_restated": "Milo Voss, Zara Okafor and Edric Thane.",
        "central_conflict": "c",
        "acts": [{"title": "A"}],
    }
    chapters = [
        {
            "title": "Intake",
            "summary": "Milo Voss arrives; Zara Okafor and Edric Thane watch.",
            "act_index": 0,
            "pov": "Milo Voss",
            "characters": ["Milo Voss", "Zara Okafor", "Edric Thane"],
        }
    ]
    beats = [
        {
            **chapters[0],
            "children": [
                {
                    "title": "Onboarding",
                    "summary": "Milo is processed.",
                    "characters": ["Milo Voss"],
                    "secondary_names": ["Prava", "Bartholomew Quince"],
                    "setting": "Intake Atrium",
                    "tone": "wry",
                    "children": [],
                }
            ],
        }
    ]
    fake = FakeLLM(
        {
            "outline:chapters": json.dumps(chapters),
            "outline:beats": json.dumps(beats),
        }
    )
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    nodes, warnings = await so.generate_staged_outline(cel, skeleton=skeleton)
    beat = nodes[0]["children"][0]
    milo = next(c for c in cel.characters if c.name == "Milo Voss")
    assert beat["character_ids"] == [str(milo.id)]
    assert beat["secondary_names"] == ["Prava"]
    assert beat["setting"] == "Intake Atrium" and beat["tone"] == "wry"
    assert any("Bartholomew Quince" in w for w in warnings)


# --- P3 / P4: the scene planner and the scene brief ----------------------------------


def test_scene_block_omits_blank_setting_and_tone():
    block = scene_block(
        {
            "scene_description": "Intake: Milo is processed.",
            "setting": "",
            "emotional_tone": "",
        }
    )
    assert not BLANK_SETTING.search(block)
    assert "Setting:" not in block and "Emotional tone:" not in block
    assert "Scene description: Intake: Milo is processed." in block


def test_scene_block_never_carries_neutral_and_keeps_real_values():
    block = scene_block(
        {"scene_description": "d", "setting": "   ", "emotional_tone": "Neutral"}
    )
    assert "neutral" not in block.lower()
    block = scene_block(
        {"scene_description": "d", "setting": "Intake Atrium", "emotional_tone": "wry"}
    )
    assert "Setting: Intake Atrium" in block and "Emotional tone: wry" in block
    assert not P3_ALSO.search(block)


def test_scene_block_lists_also_named_secondaries():
    block = scene_block(
        {"scene_description": "d", "secondary_names": ["Prava", " prava", "Old Fen"]}
    )
    assert P3_ALSO.search(block).group(1) == "Prava, Old Fen"


async def test_plan_beats_prompt_offers_an_available_cast(monkeypatch):
    fake = FakeLLM(
        {
            "plan_beats": json.dumps(
                [{"description": "Milo signs.", "characters": ["Milo Voss"]}]
            )
        }
    )
    monkeypatch.setattr(wf, "get_llm_client", lambda: fake)
    beats = await plan_scene_beats(
        {
            "scene_description": "Intake: Milo is processed.",
            "characters": ["Milo Voss", "Zara Okafor"],
            "setting": "",
            "emotional_tone": "",
            "secondary_names": ["Prava"],
        }
    )
    prompt = fake.prompts["plan_beats"][0]
    cast = [n.strip() for n in P3_CAST.search(prompt).group(1).split(",")]
    assert cast == ["Milo Voss", "Zara Okafor"]
    assert P3_ALSO.search(prompt).group(1) == "Prava"
    assert "unnamed functional figures" in prompt.lower()
    assert "cast is exactly" not in prompt
    assert not BLANK_SETTING.search(prompt)
    assert "Emotional tone:" not in prompt
    # the per-beat cast is still a subset of the scene cast
    assert beats[0]["characters"] == ["Milo Voss"]


def test_parse_structured_beats_keeps_the_subset_rule():
    beats = parse_structured_beats(
        json.dumps([{"description": "x", "characters": ["Prava", "Milo Voss"]}]),
        ["Milo Voss"],
    )
    assert beats[0]["characters"] == ["Milo Voss"]


# --- the offline stub keeps its markers and emits the beat fields ----------------------


async def test_stub_beats_carry_cast_setting_tone_and_one_secondary_set(
    monkeypatch, cel
):
    from evals.stub_llm import _answer

    class StubClient:
        def __init__(self):
            self.answers = []

        async def generate(self, messages, *, purpose, **kw):
            text = _answer(messages[0]["content"])
            self.answers.append((purpose, text))
            return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)

    client = StubClient()
    monkeypatch.setattr(so, "get_llm_client", lambda: client)
    chapters = [
        {"title": "Chapter 1: Milo Voss", "act_index": 0, "characters": ["Milo Voss"]},
        {
            "title": "Chapter 2: Zara Okafor",
            "act_index": 0,
            "characters": ["Zara Okafor", "Milo Voss"],
        },
    ]
    act1 = await so._stage_beats_for_act(cel, 0, chapters, None, skeleton={"acts": []})
    act2 = await so._stage_beats_for_act(cel, 1, chapters, None, skeleton={"acts": []})
    first = act1[0]["children"][0]
    assert first["characters"] == ["Milo Voss"]
    assert first["setting"] == "the intake atrium" and first["tone"] == "wry"
    assert "Prava" in first["secondary_names"] and "Old Fen" in first["secondary_names"]
    assert act1[1]["children"][0]["characters"] == ["Zara Okafor", "Milo Voss"]
    others = [c for n in act1 for c in n["children"]][1:] + [
        c for n in act2 for c in n["children"]
    ]
    assert others and all("secondary_names" not in c for c in others)


async def test_stub_planner_still_parses_the_new_wording(monkeypatch):
    from evals.stub_llm import _answer

    class StubClient:
        async def generate(self, messages, *, purpose, **kw):
            text = _answer(messages[0]["content"])
            return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)

    monkeypatch.setattr(wf, "get_llm_client", lambda: StubClient())
    canon_block = "<canon>\n# Characters\n\n### Milo Voss\n\n### Zara Okafor\n</canon>"
    beats = await plan_scene_beats(
        {"scene_description": "d", "characters": ["Milo Voss", "Zara Okafor"]},
        canon_block=canon_block,
    )
    assert len(beats) == 3
    assert beats[0]["characters"] == ["Milo Voss", "Zara Okafor"]
