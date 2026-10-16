"""SPEC-24 (W2-4): beat prose stays in the POV character's head.

How the POV is resolved from the request and the style guide, and the one
wide-mode rule (`Pov.one_head`). The POV line in the real planner, beat and
revise prompts is graded by the round trip (test_prompt_roundtrip.py:
pov_line_has_mode_and_character)."""

import types
import uuid

import pytest

from app.core.prompt_markers import POV_LIMIT_OPEN, POV_LINE_RE
from app.orchestration.pov import (
    Pov,
    pov_instruction,
    pov_mode,
    resolve_pov,
)

pytestmark = pytest.mark.unit


def _canon(style_pov=None, cast=("Milo Voss", "Zara Okafor"), aliases=None):
    style = types.SimpleNamespace(pov=style_pov) if style_pov is not None else None
    chars = [
        types.SimpleNamespace(
            id=uuid.uuid4(), name=n, aliases=list((aliases or {}).get(n, []))
        )
        for n in cast
    ]
    return types.SimpleNamespace(style=style, characters=chars)


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


def test_omniscient_and_objective_are_not_held_to_one_head():
    """Review: a wide mode is not told to narrate only what one character
    perceives, with or without a named POV character; it still may not run
    ahead, and says so without "after this beat", which means nothing to the
    planner or to revise (B-1). The marker line parses either way."""
    for who in (None, "Milo Voss"):
        omni = pov_instruction(Pov("third person omniscient", who))
        objective = pov_instruction(Pov("third person objective", who))
        for text in (omni, objective):
            assert POV_LIMIT_OPEN not in text and "after this beat" not in text
            assert "no aftermath, no foreshadowing" in text
            m = POV_LINE_RE.search(text)
            assert m.group("who") == who
        assert "thoughts" in omni and "seen and heard" in objective
    limited = pov_instruction(Pov("third person limited", "Milo Voss"))
    assert f"{POV_LIMIT_OPEN} Milo Voss" in limited


def test_one_head_modes_name_people_only_once_learned():
    """W3-3: an introduction in the scene counts, so no name badges."""
    rule = "learns their name in the story; an introduction in this scene counts"
    assert f"before Milo Voss {rule}" in pov_instruction(
        Pov("third person limited", "Milo Voss")
    )


def test_pov_mode():
    assert pov_mode("Third-Person Limited, on Milo") == "third person limited"
    # The short forms style.pov stores (orm_models: first | third-limited |
    # third-omniscient) keep their mode instead of falling back to the default.
    assert pov_mode("first") == "first person"
    assert pov_mode("third-limited") == "third person limited"
    assert pov_mode("third-omniscient") == "third person omniscient"
    assert pov_mode("close third") == "third person limited"
    assert pov_mode("prose") is None


def test_one_head_shows_only_what_the_pov_sees_of_a_brief():
    """W4-3 (M6): a brief may state another person's intent; a one-head POV
    shows only what its character can see or hear of it (F). An omniscient
    narrator may tell it (R)."""
    limit = "The brief may say what other people intend or feel; show only what"
    text = pov_instruction(Pov("third person limited", "Milo Voss"))
    assert text.endswith(f"{limit} Milo Voss can see or hear.")
    assert limit not in pov_instruction(Pov("third person omniscient", "Milo"))


def test_no_foreshadowing_names_its_example_in_both_branches():
    """(F) SPEC-48 (D10): the hindsight example, in both branches."""
    example = "no foreshadowing — never 'would later learn'"
    for pov in (
        Pov("third person limited", "Milo Voss"),
        Pov("third person omniscient"),
    ):
        assert example in pov_instruction(pov)
