"""SPEC-24 (W2-2): named secondary people — the prompt list and the author's
"Add <name> to cast" suggestions."""

import types
import uuid

import pytest

from app.planning.canon import Canon
from app.planning.secondaries import (
    SENTENCE_CAP,
    secondary_candidates,
    suggested_characters,
)
from evals.steps.pipeline import load_corpus

pytestmark = pytest.mark.unit


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


def _entry(name, content="", category="org"):
    return types.SimpleNamespace(name=name, content=content, category=category)


@pytest.fixture
def cel():
    _text, gt = load_corpus("cel")
    cast = [
        _person("Milo Voss", ["Milo"]),
        _person("Zara Okafor", ["Zara"], role="deuteragonist"),
        _person(
            "Edric Thane",
            ["Edric", "Chief Executive Lich", "the Lich", "the CEL"],
            role="antagonist",
        ),
    ]
    return Canon(title="Bored to Undeath", synopsis=gt["synopsis"], characters=cast)


# --- secondary_candidates -----------------------------------------------------


def test_cel_candidates_include_the_synopsis_secondaries(cel):
    names = secondary_candidates(cel)
    assert "Prava" in names and "Old Fen" in names
    # ordered by first appearance in the synopsis
    assert names.index("Prava") < names.index("Old Fen")


def test_cel_candidates_exclude_cast_names_aliases_and_title(cel):
    names = secondary_candidates(cel)
    lowered = {n.lower() for n in names}
    for excluded in (
        "milo voss",
        "milo",
        "zara okafor",
        "zara",
        "edric thane",
        "edric",
        "chief executive lich",
        "lich",
        "cel",
        "bored to undeath",
    ):
        assert excluded not in lowered, excluded
    # no heading or acronym, no sentence-initial common word
    assert not any(n.isupper() for n in names)
    assert "Death" not in names and "Fen" not in names  # Old Fen is listed whole


def test_candidates_exclude_canon_entry_names():
    canon = Canon(
        title="T",
        synopsis="Milo Voss visits Aeon Holdings, where Prava works in Marrow District.",
        characters=[_person("Milo Voss")],
        canon_entries=[_entry("Aeon Holdings"), _entry("Marrow District", "A place.")],
    )
    assert secondary_candidates(canon) == ["Prava"]


def test_candidates_skip_bible_labels_and_headings():
    milo = _person("Milo Voss")
    milo.description = "Works beside Prava every day."
    canon = Canon(title="T", synopsis="", characters=[milo])
    names = secondary_candidates(canon)
    assert names == ["Prava"]  # not "Description", "Role" or "Characters"


def test_candidates_empty_canon():
    assert secondary_candidates(Canon(title="T")) == []


# --- suggested_characters ---------------------------------------------------------


def _outline(*secondaries_per_beat, top=None):
    node = {
        "title": "Ch",
        "summary": "",
        "children": [
            {"title": f"b{i}", "summary": "", "secondary_names": list(s)}
            for i, s in enumerate(secondaries_per_beat)
        ],
    }
    if top is not None:
        node["secondary_names"] = top
    return [node]


def test_suggestions_come_from_declared_secondaries_in_the_canon(cel):
    nodes = _outline(["Prava", "Bartholomew Quince"], ["Old Fen"])
    out = suggested_characters(nodes, cel)
    assert [s["name"] for s in out] == ["Prava", "Old Fen"]
    prava = out[0]["synopsis_sentence"]
    assert "Prava" in prava and prava.startswith("There is Prava")
    assert len(prava) <= SENTENCE_CAP
    fen = out[1]["synopsis_sentence"]
    assert fen.startswith("There is Old Fen") and fen.endswith(".")


def test_suggestions_exclude_the_current_cast(cel):
    nodes = _outline(["Prava", "Milo", "the Lich", "Zara Okafor"])
    assert [s["name"] for s in suggested_characters(nodes, cel)] == ["Prava"]
    # once the author adds her, she is no longer suggested
    cel.characters.append(_person("Prava", role="minor"))
    assert suggested_characters(nodes, cel) == []


def test_suggestions_dedupe_case_insensitively_in_outline_order(cel):
    nodes = _outline(["old fen"], ["Prava", "OLD FEN"], top=["Prava"])
    assert [s["name"] for s in suggested_characters(nodes, cel)] == [
        "Prava",
        "old fen",
    ]


def test_suggestion_absent_from_synopsis_and_canon_is_never_offered(cel):
    assert suggested_characters(_outline(["Elara", "Corporeal"]), cel) == []
    assert suggested_characters([], cel) == []
    assert suggested_characters("not a list", cel) == []


def test_sentence_falls_back_to_canon_entries():
    canon = Canon(
        title="T",
        synopsis="Milo Voss works.",
        characters=[_person("Milo Voss")],
        canon_entries=[
            _entry("Records", "The filing floor. Old Fen keeps it. He never leaves.")
        ],
    )
    out = suggested_characters(_outline(["Old Fen"]), canon)
    assert out == [{"name": "Old Fen", "synopsis_sentence": "Old Fen keeps it."}]


def test_sentence_is_capped_at_a_word_boundary():
    long = "Prava " + "files and files " * 60 + "forever."
    canon = Canon(title="T", synopsis=long, characters=[_person("Milo Voss")])
    sentence = suggested_characters(_outline(["Prava"]), canon)[0]["synopsis_sentence"]
    assert len(sentence) <= SENTENCE_CAP and sentence.endswith("…")
    assert sentence.startswith("Prava files")
