"""SPEC-24 (W2-4): beat prose stays in the POV character's head.

P5 (the POV line: mode + character, then "Narrate only what <who> perceives
or knows"), P6 (the last beat ends inside the moment) and P7 (the existing
markers) are the strings E6's prompt_contract grader greps for
(tpm-plan-wave2 §1.3)."""

import re
import types
import uuid

import pytest

from app.core.prompt_markers import POV_LINE_RE
from app.orchestration import prose as prose_module
from app.orchestration.prose import (
    Pov,
    pov_instruction,
    pov_mode,
    resolve_pov,
    write_beat_prose,
)

pytestmark = pytest.mark.unit

P5 = re.compile(
    r"(?m)^Point of view: (?P<mode>(first|second|third) person[^,\n]*), "
    r"(?P<who>[^.\n]+)\."
)
P6 = "end the scene inside the moment, in POV; no summary of what follows"


def _canon(style_pov=None, cast=("Milo Voss", "Zara Okafor"), aliases=None):
    style = types.SimpleNamespace(pov=style_pov) if style_pov is not None else None
    chars = [
        types.SimpleNamespace(
            id=uuid.uuid4(), name=n, aliases=list((aliases or {}).get(n, []))
        )
        for n in cast
    ]
    return types.SimpleNamespace(style=style, characters=chars)


class FakeClient:
    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return types.SimpleNamespace(text="Prose.", tokens_in=1, tokens_out=1)


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(prose_module, "get_llm_client", lambda: client)
    return client


_BEATS = [
    {"description": "Milo finds the ledger.", "characters": ["Milo Voss"]},
    {"description": "Zara reads it aloud.", "characters": ["Zara Okafor"]},
    {"description": "The lights go out.", "characters": []},
]


# --- resolving the POV --------------------------------------------------------------


def test_request_character_with_the_style_guide_mode():
    pov = resolve_pov(
        {"pov_character": "Milo Voss"}, _canon("third person limited, on Milo Voss")
    )
    assert pov == Pov("third person limited", "Milo Voss")
    assert pov.label == "third person limited, Milo Voss"


def test_no_style_falls_back_to_third_person_limited():
    assert resolve_pov({"pov_character": "Milo Voss"}, _canon()) == (
        Pov("third person limited", "Milo Voss")
    )
    assert resolve_pov({}, _canon()) == Pov("third person limited", None)
    assert Pov("third person limited").label == "third person limited"


def test_mode_comes_from_the_style_guide_never_from_the_name():
    canon = _canon("First-person, present tense")
    assert resolve_pov({"pov_character": "Zara Okafor"}, canon) == (
        Pov("first person", "Zara Okafor")
    )
    # the retired request key is not read
    assert resolve_pov(
        {"pov_character": "Zara Okafor", "pov_mode": "second person"}, canon
    ) == Pov("first person", "Zara Okafor")
    # a style guide that names no mode ("third") does not invent one
    assert resolve_pov({"pov_character": "Zara Okafor"}, _canon("third")) == (
        Pov("third person limited", "Zara Okafor")
    )


def test_style_guide_character_is_used_only_when_it_is_cast():
    assert resolve_pov({}, _canon("third person limited, on Milo Voss")) == (
        Pov("third person limited", "Milo Voss")
    )
    assert resolve_pov({}, _canon("first person, Milo")) == (
        Pov("first person", "Milo Voss")
    )
    assert resolve_pov({}, _canon("Close third, on Zara")) == (
        Pov("third person limited", "Zara Okafor")
    )
    assert resolve_pov({}, _canon("third person omniscient, wry")) == (
        Pov("third person omniscient", None)
    )
    assert resolve_pov({}, _canon("first person, Bartholomew Quince")) == (
        Pov("first person", None)
    )


def test_a_titled_style_character_resolves_through_the_cast():
    """Review S6: "Dr. Ada Vance" is not a cast form, but its capitalised run
    resolves once the title is dropped."""
    canon = _canon(
        "third person limited, on Dr. Ada Vance",
        cast=("Ada Vance", "Milo Voss"),
        aliases={"Ada Vance": ["Dr. Vance"]},
    )
    pov = resolve_pov({}, canon)
    assert pov == Pov("third person limited", "Ada Vance")
    m = POV_LINE_RE.search(pov_instruction(pov))
    assert m.group("mode") == "third person limited"
    assert m.group("who") == "Ada Vance"


def test_pov_mode():
    assert pov_mode("Third-Person Limited, on Milo") == "third person limited"
    assert pov_mode("close third") is None


def test_pov_instruction_matches_p5():
    text = pov_instruction(Pov("third person limited", "Milo Voss"))
    m = P5.search(text)
    assert m and m.group("mode") == "third person limited"
    assert m.group("who") == "Milo Voss"
    assert text.startswith("Point of view: third person limited, Milo Voss.")
    assert "Narrate only what Milo Voss perceives or knows" in text
    assert "\n" not in text  # one paragraph
    text = pov_instruction(Pov("first person"))
    assert text.startswith("Point of view: first person.")
    assert POV_LINE_RE.search(text).group("who") is None


# --- the beat prompt ------------------------------------------------------------------


async def test_beat_prompt_has_the_pov_line_and_its_rule(fake):
    pov = resolve_pov(
        {"pov_character": "Milo Voss"}, _canon("third person limited, on Milo Voss")
    )
    await write_beat_prose(
        _BEATS[0],
        {"scene_description": "Intake.", "setting": "", "emotional_tone": ""},
        "cast",
        "",
        pov=pov,
        all_beats=_BEATS,
        beat_index=0,
    )
    prompt = fake.prompts[0]
    m = P5.search(prompt)
    assert m and m.group("who") == "Milo Voss"
    rest = prompt[m.end() :].split("\n\n", 1)[0]
    assert rest.lstrip().startswith("Narrate only what Milo Voss perceives or knows")
    # P4: no blank setting, no default tone
    assert not re.search(r"(?m)^\s*Setting:\s*$", prompt)
    assert "Emotional tone:" not in prompt
    # P7: the existing markers survive
    for marker in ("CHARACTERS IN THIS BEAT", "BEAT TO WRITE", "<beats>"):
        assert marker in prompt
    assert "Stop where beat 2 begins" in prompt
    assert P6 not in prompt


async def test_last_beat_ends_inside_the_moment(fake):
    await write_beat_prose(
        _BEATS[2],
        {"scene_description": "d"},
        "cast",
        "Before.",
        pov=Pov("third person limited", "Milo Voss"),
        all_beats=_BEATS,
        beat_index=2,
    )
    prompt = fake.prompts[0]
    assert f"Write ONLY beat 3, the last beat: {P6}." in prompt
    assert "end the scene here" not in prompt
    assert "Stop where beat" not in prompt
    assert "<prior>" in prompt


async def test_default_pov_without_a_character(fake):
    await write_beat_prose(_BEATS[0], {"scene_description": "d"}, "cast", "")
    assert "Point of view: third person limited. Narrate only what" in fake.prompts[0]
