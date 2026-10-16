"""SPEC-24 (W2-1 prompt half, W2-2, §1e): each outline chapter and beat lists
its `people`, which the server classifies once (cast ids, canon-named
secondaries, inventions); the scene planner treats the cast as available;
blank settings and the "neutral" tone never reach a prompt. The prompt wording
itself is graded by the eval round trip (SPEC-28), not pinned here."""

import json
import re
import types
import uuid

import pytest

from app.orchestration.workflow import parse_structured_beats, scene_block
from app.planning import staged_outline as so
from app.planning.canon import Canon
from app.planning.outline import validate_outline_nodes
from evals.steps.pipeline import load_corpus

pytestmark = pytest.mark.unit

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


# --- §1.1: validating the beat children -------------------------------------------


def _cast():
    return [_person("Milo Voss", ["Milo"]), _person("Zara Okafor", ["Zara"])]


def test_children_resolve_cast_and_keep_setting_tone_secondaries():
    """Legacy `characters` + `secondary_names` input (stored outlines, old
    archives) goes through the same classification as `people`."""
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
    # with no canon text to vet them, unresolved names are secondary
    assert beat["character_ids"] == [str(milo.id), str(zara.id)]
    assert beat["secondary_names"] == ["Ghost", "Prava"]
    assert beat["setting"] == "Intake Atrium"
    assert beat["tone"] == "bureaucratic dread"
    assert "character_ids" not in bare and "setting" not in bare
    # a restore (no cast) and a re-validation are byte-identical
    assert validate_outline_nodes(nodes) == nodes
    assert validate_outline_nodes(nodes, cast=[milo, zara]) == nodes


def test_strict_keeps_unknown_names_secondary_and_rejects_a_dead_id():
    milo, zara = _cast()
    nodes = validate_outline_nodes(
        [
            {
                "title": "Ch",
                "children": [{"title": "b", "people": ["Milo", "Prava", "Elara"]}],
            }
        ],
        cast=[milo, zara],
        strict=True,
    )
    beat = nodes[0]["children"][0]
    assert beat["character_ids"] == [str(milo.id)]
    assert beat["secondary_names"] == ["Prava", "Elara"]
    gone = str(uuid.uuid4())
    with pytest.raises(ValueError, match=gone):
        validate_outline_nodes(
            [{"title": "Ch", "children": [{"title": "b", "people": [gone]}]}],
            cast=[milo, zara],
            strict=True,
        )
    with pytest.raises(ValueError, match="Ghost"):
        validate_outline_nodes(
            [{"title": "Ch", "pov": "Ghost"}], cast=[milo, zara], strict=True
        )


def test_generation_classifies_people_against_the_canon_text(cel):
    from app.parsing import names
    from app.planning.fidelity import canon_known_text

    milo = cel.characters[0]
    known = names.norm(canon_known_text(cel))
    people = {
        "title": "Ch",
        "pov": "Milo",
        "people": ["Milo", "Prava", "Elara"],
        "children": [{"title": "b", "people": ["Milo Voss", "Old Fen", "Elara"]}],
    }
    legacy = {
        "title": "Ch",
        "pov": "Milo",
        "characters": ["Milo", "Prava"],
        "secondary_names": ["Elara"],
        "children": [
            {
                "title": "b",
                "characters": ["Milo Voss"],
                "secondary_names": ["Old Fen", "Elara"],
            }
        ],
    }
    out = []
    for node in (people, legacy):
        [top] = validate_outline_nodes([node], cast=cel.characters, known_text=known)
        assert top["character_ids"] == [str(milo.id)]
        assert top["secondary_names"] == ["Prava"]  # Elara is dropped
        assert top["children"][0]["secondary_names"] == ["Old Fen"]
        out.append({k: v for k, v in top.items() if k != "node_id"})
        out[-1]["children"] = [
            {k: v for k, v in c.items() if k != "node_id"} for c in top["children"]
        ]
    assert out[0] == out[1]


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
            "people": ["Milo Voss", "Zara Okafor", "Edric Thane"],
        }
    ]
    beats = [
        {
            **chapters[0],
            "children": [
                {
                    "title": "Onboarding",
                    "summary": "Milo is processed.",
                    "people": ["Milo Voss", "Prava", "Bartholomew Quince"],
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


def test_scene_block_keeps_an_authors_tone_and_real_values():
    """Review S7: only a blank tone is omitted; "Neutral" is the author's."""
    block = scene_block(
        {"scene_description": "d", "setting": "   ", "emotional_tone": "Neutral"}
    )
    assert "Emotional tone: Neutral" in block and "Setting:" not in block
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


def test_parse_structured_beats_keeps_the_subset_rule():
    beats = parse_structured_beats(
        json.dumps([{"description": "x", "characters": ["Prava", "Milo Voss"]}]),
        ["Milo Voss"],
    )
    assert beats[0]["characters"] == ["Milo Voss"]


# --- the offline stub keeps its markers and emits the beat fields ----------------------


async def test_stub_beats_carry_people_setting_tone_and_the_synopsis_people(
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
        {"title": "Chapter 1: Milo Voss", "act_index": 0, "people": ["Milo Voss"]},
        {
            "title": "Chapter 2: Zara Okafor",
            "act_index": 0,
            "people": ["Zara Okafor", "Milo Voss"],
        },
    ]
    outline = chapters + [{**c, "act_index": 1} for c in chapters]
    act1, act2 = [
        await so._stage_beats_for_act(
            cel, act, None, skeleton={"acts": []}, outline=outline, earlier=[]
        )
        for act in (0, 1)
    ]
    first = act1[0]["children"][0]
    assert first["people"][0] == "Milo Voss" and "Prava" in first["people"]
    assert first["setting"] == "the intake atrium" and first["tone"] == "wry"
    assert act1[1]["children"][0]["people"] == ["Zara Okafor", "Milo Voss"]
    others = [c for n in act1 for c in n["children"]][1:] + [
        c for n in act2 for c in n["children"]
    ]
    assert others and all("Prava" not in c["people"] for c in others)
