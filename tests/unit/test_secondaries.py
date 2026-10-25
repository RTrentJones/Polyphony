"""SPEC-24 (W2-2): the author's "Add <name> to cast" suggestions come only
from people the model declared, classified against today's cast and canon."""

import re
import types
import uuid

import pytest

from app.characters.context import speech_paragraphs
from app.parsing import character_extractor as ce
from app.planning.canon import Canon
from app.planning.secondaries import (
    NO_SAMPLES_NOTE,
    SENTENCE_CAP,
    introduction,
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
    assert out == [
        {
            "name": "Old Fen",
            "synopsis_sentence": "Old Fen keeps it.",
            "introduction": "Old Fen keeps it.",
        }
    ]


def test_sentence_is_capped_at_a_word_boundary():
    long = "Prava " + "files and files " * 60 + "forever."
    canon = Canon(title="T", synopsis=long, characters=[_person("Milo Voss")])
    sentence = suggested_characters(["Prava"], canon)[0]["synopsis_sentence"]
    assert len(sentence) <= SENTENCE_CAP and sentence.endswith("…")
    assert sentence.startswith("Prava files")


# --- the scene's secondary block (SPEC-29) ------------------------------------------


def _secondaries(canon, names, text, later_from=None):
    return scene_secondaries(names, canon, [("s1", text)], later_from or {})


def test_secondaries_get_their_introduction_and_own_lines(cel, monkeypatch):
    calls = []
    real = ce.attribute_speech
    monkeypatch.setattr(ce, "attribute_speech", lambda t: calls.append(1) or real(t))
    fen, prava = _secondaries(cel, ["Old Fen", "Prava"], load_excerpts("cel"))
    assert calls == [1]  # once per source, not once per name
    assert fen.intro_sentence.startswith("Old Fen, who has been in Records")
    assert fen.samples == ["Nobody reads the appendices. That's what they're for."]
    assert prava.intro_sentence.startswith("Prava, the intake caseworker")
    assert prava.samples == []
    block = render_secondaries([fen, prava])
    assert block.startswith("<secondary>\n### Old Fen\nIntroduced: Old Fen, who")
    assert '- "Nobody reads the appendices.' in block
    assert block.split("### Prava")[1].count(NO_SAMPLES_NOTE) == 1
    assert len(block) <= 1500  # the cost guard, on the cel fixtures
    assert render_secondaries([]) == ""


def test_a_tag_two_cast_members_share_credits_nobody():
    """A "said Okafor" tag matches two cast members: a secondary called Okafor
    is never credited with it."""
    canon = Canon(title="T", characters=[_person("Zara Okafor"), _person("Ada Okafor")])
    text = '"Close every account tonight," Okafor said.'
    (okafor,) = _secondaries(canon, ["Okafor"], text)
    assert okafor.samples == []


# --- SPEC-40: introductions and later plot (W5-1 (b), (c)) ---------------------------


INTROS = {  # (F) through the first appositive, from the name (SPEC-57)
    "Prava": "Prava, the intake caseworker who onboarded Milo with such "
    "practiced warmth",
    "Old Fen": "Old Fen, who has been in Records so long that he has begun "
    "to file himself",
    "Halvard Bell": "Halvard Bell had been a dentist, then a soul in Retention for "
    "ninety years",
}


def test_the_prompt_introduction_ends_at_the_first_appositive(cel):
    """(F) The scene block's introduction; (R) the "Add to cast" one is whole."""
    people = _secondaries(cel, list(INTROS), load_excerpts("cel"))
    assert {p.name: p.intro_sentence for p in people} == INTROS
    (offer,) = suggested_characters(["Prava"], cel)
    assert "private ledger" in offer["synopsis_sentence"]
    assert offer["introduction"] == INTROS["Prava"]  # (F) W6-5 (a); was ""
    lead = "On his first day, Prava, the intake caseworker, processes his file."
    assert introduction(lead, "Prava") == "Prava, the intake caseworker"  # (F)
    assert introduction(lead, "prava") == "Prava, the intake caseworker"  # (R)


@pytest.mark.parametrize(
    "sentence, intro",
    [  # (F) SPEC-57: "There is / was / are" goes when the name follows it
        ("There was Prava, who files, and sorts.", "Prava, who files"),
        ("there are Prava and Fen, who sort, and file.", "Prava and Fen, who sort"),
        # (R) the comma rule, as before; (R) other words before the name stay
        ("There is a clerk, Prava, who files, and sorts.", "Prava, who files"),
        (
            "There was a clerk called Prava, who files.",
            "There was a clerk called Prava, who files.",
        ),
    ],
)
def test_an_introduction_starts_at_the_name(sentence, intro):
    assert introduction(sentence, "prava") == intro


async def test_the_owned_note_never_stands_without_its_block(monkeypatch):
    """(R) Q12's guard: `OWNED_PHRASES_NOTE` stands directly over a filled
    <owned> block when an absent member owns a phrase, and not otherwise."""
    import app.characters.context as ctx
    from tests.unit.factories import make_scope

    store = types.SimpleNamespace(retrieve_similar=lambda **kw: _none())
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    kw = dict(aliases=[], user_id=None, book_id=None)
    milo = types.SimpleNamespace(id=uuid.uuid4(), name="Milo Voss", **kw)
    edric = types.SimpleNamespace(id=uuid.uuid4(), name="Edric Thane", **kw)
    milo.voice_characteristics, edric.voice_characteristics = None, "says 'Mr. Voss'"
    absent = await ctx.build_cast_context(
        [milo], "x", scope=make_scope(book_cast=[milo, edric])
    )
    assert re.search(
        re.escape(ctx.OWNED_PHRASES_NOTE) + r"\n<owned>\n\S[^\n]*\n</owned>", absent
    )
    present = await ctx.build_cast_context(
        [milo, edric], "x", scope=make_scope(book_cast=[milo, edric])
    )
    assert ctx.OWNED_PHRASES_NOTE not in present


async def _none(*_):
    return []


async def test_scene_setup_builds_plot_and_later_plot(
    async_session, test_book, test_source, cel
):
    """(F) `plot` is the brief, the book's plan lines and the chapter's earlier
    written beat lines; the excerpt's later plot begins at W4's cut (14), and a
    secondary's own lines stop at it (Old Fen's, paragraph 12, past W4b2's 9)."""
    import json
    from pathlib import Path

    from app.orchestration.prose import narration_excerpts, scene_setup
    from app.planning.planned_beat import beat_scene_description
    from tests.unit.factories import add_planned_chapter, add_scene, add_scene_beats

    fixture = Path(__file__).parents[1] / "fixtures" / "w4_outlines.json"
    plan = json.loads(fixture.read_text())["w4"]
    test_book.synopsis = load_corpus("cel")[1]["synopsis"]
    chapters = [
        await add_planned_chapter(
            async_session, test_book, [b for b in plan if b["chapter"] == n], n - 1
        )
        for n in range(1, 7)
    ]
    first = await add_scene(async_session, test_book, chapters[0])
    await add_scene_beats(async_session, first, ["Milo signs.", "Prava smiles."])
    scene = await add_scene(async_session, test_book, chapters[0], 1)
    test_source.content_text = load_excerpts("cel")
    await async_session.commit()
    request = {"scene_description": "Brief", "secondary_names": ["Prava"]}
    setup = await scene_setup(async_session, test_book.id, request, scene.id)
    lines = [beat_scene_description(b) for b in plan]
    assert setup.scope.plot == ("Brief", *lines, "Milo signs.", "Prava smiles.")
    unbriefed = await scene_setup(async_session, test_book.id, {}, scene.id)
    assert unbriefed.scope.plot[:2] == ("", lines[0])  # (R) the brief keeps [0]
    assert setup.scope.later_from == {str(test_source.id): 14}
    speech = speech_paragraphs(load_excerpts("cel"))  # SPEC-77: keyed by str(id)
    assert setup.scope.own_plot == ("Brief",) and 2 in speech
    assert setup.scope.speech_paragraphs == {str(test_source.id): speech}
    assert "practiced warmth\n" in setup.secondary_block
    (fen,) = _secondaries(cel, ["Old Fen"], load_excerpts("cel"), {"s1": 9})
    assert fen.samples == []
    # W18-5 (B1): cel's eligible paragraphs before the cut (3 and 9) each
    # restate a W4 plan line, so none is a style excerpt; (R) planless, both are.
    assert setup.narration == ()
    paragraphs = [
        " ".join(p.split()) for p in ce.source_paragraphs(load_excerpts("cel"))
    ]
    planless = narration_excerpts([("s", load_excerpts("cel"))], {"s": 14}, ["Brief"])
    assert planless == (paragraphs[3], paragraphs[9])
