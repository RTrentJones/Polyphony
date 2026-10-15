"""Day-0 contract (wave 2.5 §1b): classify_people and the names surface."""

import types
import uuid

import pytest

from app.parsing import names
from app.parsing.names import mentions, norm
from app.planning.fidelity import canon_known_text
from app.planning.outline import CastIndex, People, classify_people
from app.planning.planned_beat import MAX_SECONDARY_NAMES

pytestmark = pytest.mark.unit


def _person(name, aliases=()):
    return types.SimpleNamespace(id=uuid.uuid4(), name=name, aliases=list(aliases))


@pytest.fixture
def cast():
    milo = _person("Milo Voss", ["Milo"])
    zara = _person("Zara Okafor", ["Zara"])
    edric = _person("Edric Thane", ["the Lich"])
    return types.SimpleNamespace(
        index=CastIndex([milo, zara, edric]), milo=milo, zara=zara, edric=edric
    )


def _known(synopsis, entries=()):
    canon = types.SimpleNamespace(synopsis=synopsis, bible=lambda: "\n".join(entries))
    return norm(canon_known_text(canon))


def test_alias_and_id_resolve_in_first_mention_order(cast):
    out = classify_people(
        ["the Lich", str(cast.milo.id), "Milo", cast.zara.id], cast.index, None
    )
    assert out == People(
        [str(cast.edric.id), str(cast.milo.id), str(cast.zara.id)], [], []
    )


def test_prava_is_secondary_when_the_canon_names_her(cast):
    known = _known("Milo meets Prava, the intake caseworker.")
    assert classify_people(["Prava"], cast.index, known).secondary == ["Prava"]
    out = classify_people(["Prava"], cast.index, _known("Milo works alone."))
    assert out == People([], [], ["Prava"])


def test_a_dead_uuid_is_invented_never_a_name(cast):
    dead = str(uuid.uuid4())
    assert classify_people([dead], cast.index, None) == People([], [], [dead])
    assert classify_people([uuid.UUID(dead)], cast.index, None).invented == [dead]


def test_known_text_none_makes_every_unresolved_name_secondary(cast):
    out = classify_people(["Milo", "Prava", "Elara"], cast.index, None)
    assert out == People([str(cast.milo.id)], ["Prava", "Elara"], [])


def test_skips_blank_and_non_name_refs_and_collapses_whitespace(cast):
    out = classify_people(
        ["", "   ", None, 7, {"name": "x"}, "  Old \n Fen  ", "x" * 150],
        cast.index,
        None,
    )
    assert out.secondary == ["Old Fen", "x" * 100]
    assert out.ids == [] and out.invented == []


def test_secondary_is_deduped_case_insensitively_and_capped(cast):
    many = [f"Person {chr(65 + i)}" for i in range(15)]
    out = classify_people(["Prava", "PRAVA", "prava", *many], cast.index, None)
    assert out.secondary[0] == "Prava"
    assert len(out.secondary) == MAX_SECONDARY_NAMES == 12
    assert out.secondary[1:] == many[:11]  # names past the cap are dropped
    assert out.invented == []


def test_never_raises(cast):
    assert classify_people(None, cast.index, None) == People([], [], [])
    assert classify_people(["Milo"], None, None) == People([], ["Milo"], [])


def test_halvard_bell(cast):
    """Eval finding 3: a person the synopsis names is secondary, not invented."""
    known = _known("Milo audits the firm. Halvard Bell signs off on everything.")
    out = classify_people(["Milo Voss", "Halvard Bell"], cast.index, known)
    assert out == People([str(cast.milo.id)], ["Halvard Bell"], [])
    known = _known("Milo inherits the Bell account.")
    assert classify_people(["Bell"], cast.index, known).secondary == ["Bell"]
    assert classify_people(["Halvard Bell"], cast.index, known).invented == [
        "Halvard Bell"
    ]


# --- names.mentions / NON_NAME_TOKENS ------------------------------------------


@pytest.mark.parametrize(
    "text, name, expected",
    [
        ("There is Prava, the caseworker.", "Prava", True),  # punctuation
        ("There is Prava, the caseworker.", "Prava,", True),
        ("PRAVA FILES", "prava", True),  # case
        ("Old Fen keeps Records.", "old fen", True),
        ("Mr. Voss sighs.", "Mr Voss", True),
        ("Fenwick keeps Records.", "Fen", False),  # partial word
        ("Old Fen keeps Records.", "Fenw", False),
        ("Fen", "Fen Wick", False),
        ("Prava", "", False),  # an empty name is never mentioned
        ("Prava", "  ,  ", False),
        ("", "Prava", False),
    ],
)
def test_mentions(text, name, expected):
    assert mentions(norm(text), name) is expected


def test_non_name_tokens_moved_from_canon_extractor():
    from app.parsing import canon_extractor

    assert not hasattr(canon_extractor, "_NON_NAME_TOKENS")
    assert canon_extractor.NON_NAME_TOKENS is names.NON_NAME_TOKENS
    assert {"old", "the", "van"} <= names.NON_NAME_TOKENS
    assert canon_extractor._name_tokens("Old Fen") == ["Fen"]
