"""SPEC-24 (W2-2): the author's "Add <name> to cast" suggestions come only
from people the model declared, classified against today's cast and canon."""

import types
import uuid

import pytest

from app.core.orm_models import Source
from app.parsing import character_extractor as ce
from app.planning.canon import Canon
from app.planning.secondaries import (
    NO_SAMPLES_NOTE,
    SENTENCE_CAP,
    render_secondaries,
    scene_secondaries,
    suggested_characters,
)
from evals.steps.pipeline import load_corpus, load_excerpts

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


def _declared(nodes):
    """The declared names, node before children (what plans.py collects)."""
    out = []
    for node in nodes:
        out.extend(node.get("secondary_names") or [])
        out.extend(_declared(node.get("children") or []))
    return out


def test_suggestions_come_from_declared_secondaries_in_the_canon(cel):
    nodes = _declared(_outline(["Prava", "Bartholomew Quince"], ["Old Fen"]))
    out = suggested_characters(nodes, cel)
    assert [s["name"] for s in out] == ["Prava", "Old Fen"]
    prava = out[0]["synopsis_sentence"]
    assert "Prava" in prava and prava.startswith("There is Prava")
    assert len(prava) <= SENTENCE_CAP
    fen = out[1]["synopsis_sentence"]
    assert fen.startswith("There is Old Fen") and fen.endswith(".")


def test_suggestions_exclude_the_current_cast(cel):
    nodes = _declared(_outline(["Prava", "Milo", "the Lich", "Zara Okafor"]))
    assert [s["name"] for s in suggested_characters(nodes, cel)] == ["Prava"]
    # once the author adds her, she is no longer suggested
    cel.characters.append(_person("Prava", role="minor"))
    assert suggested_characters(nodes, cel) == []


def test_suggestions_dedupe_case_insensitively_in_outline_order(cel):
    from app.api.plans import _suggested_characters

    nodes = _outline(["old fen"], ["Prava", "OLD FEN"], top=["Prava"])
    assert [s["name"] for s in suggested_characters(_declared(nodes), cel)] == [
        "Prava",
        "old fen",
    ]
    # plans.py collects the same names, node before children
    assert [s["name"] for s in _suggested_characters(nodes, cel)] == [
        "Prava",
        "old fen",
    ]
    assert _suggested_characters("not a list", cel) == []


def test_suggestions_come_only_from_declared_people_even_a_place(cel):
    """§7 risk 2: "Aeon Holdings" is a canon phrase, so if the model declares
    it a person it is offered; nothing undeclared is ever suggested."""
    assert suggested_characters([], cel) == []
    out = suggested_characters(["Aeon Holdings", "Old Fen", "Prava"], cel)
    assert [s["name"] for s in out] == ["Aeon Holdings", "Old Fen", "Prava"]


def test_suggestion_absent_from_synopsis_and_canon_is_never_offered(cel):
    assert suggested_characters(_declared(_outline(["Elara", "Corporeal"])), cel) == []
    assert suggested_characters([], cel) == []


def test_sentence_falls_back_to_canon_entries():
    canon = Canon(
        title="T",
        synopsis="Milo Voss works.",
        characters=[_person("Milo Voss")],
        canon_entries=[
            _entry("Records", "The filing floor. Old Fen keeps it. He never leaves.")
        ],
    )
    out = suggested_characters(["Old Fen"], canon)
    assert out == [{"name": "Old Fen", "synopsis_sentence": "Old Fen keeps it."}]


def test_sentence_is_capped_at_a_word_boundary():
    long = "Prava " + "files and files " * 60 + "forever."
    canon = Canon(title="T", synopsis=long, characters=[_person("Milo Voss")])
    sentence = suggested_characters(["Prava"], canon)[0]["synopsis_sentence"]
    assert len(sentence) <= SENTENCE_CAP and sentence.endswith("…")
    assert sentence.startswith("Prava files")


# --- the scene's secondary block (SPEC-29) ------------------------------------------


async def _secondaries(session, book, canon, names, text):
    session.add(
        Source(book_id=book.id, user_id=book.user_id, title="s", content_text=text)
    )
    await session.commit()
    return await scene_secondaries(session, book.id, names, canon)


async def test_secondaries_get_their_introduction_and_own_lines(
    async_session, test_book, cel, monkeypatch
):
    calls = []
    real = ce.attribute_speech
    monkeypatch.setattr(ce, "attribute_speech", lambda t: calls.append(1) or real(t))
    fen, prava = await _secondaries(
        async_session, test_book, cel, ["Old Fen", "Prava"], load_excerpts("cel")
    )
    assert calls == [1]  # once per source, not once per name
    assert fen.intro_sentence.startswith("There is Old Fen, who has been in Records")
    assert fen.samples == ["Nobody reads the appendices. That's what they're for."]
    assert prava.intro_sentence.startswith("There is Prava, the intake caseworker")
    assert prava.samples == []
    block = render_secondaries([fen, prava])
    assert block.startswith("<secondary>\n### Old Fen\nIntroduced: There is Old Fen")
    assert '- "Nobody reads the appendices.' in block
    assert block.split("### Prava")[1].count(NO_SAMPLES_NOTE) == 1
    assert len(block) <= 1500  # the cost guard, on the cel fixtures
    assert render_secondaries([]) == ""


async def test_a_tag_two_cast_members_share_credits_nobody(async_session, test_book):
    """A "said Okafor" tag matches two cast members: a secondary called Okafor
    is never credited with it."""
    canon = Canon(title="T", characters=[_person("Zara Okafor"), _person("Ada Okafor")])
    text = '"Close every account tonight," Okafor said.'
    (okafor,) = await _secondaries(async_session, test_book, canon, ["Okafor"], text)
    assert okafor.samples == []
