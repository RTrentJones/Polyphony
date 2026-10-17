"""The BRD §7 acceptance gate, deterministic and free (SPEC-06).

Runs the staged outline generator over the synthetic `cel` corpus canon (the
"Bored to Undeath" incident) with a stubbed model:

- an "Elara" outline — the incident's actual failure mode — must FAIL the
  principal-cast gate, trigger exactly one repair, and still be saved with
  warnings (never a hard fail on a heuristic, R1.6);
- a faithful outline must pass with no repair.

No key, no network: this runs on every PR in test.yml, so a change that stops
the cast reaching the gate (or weakens the gate) fails CI before merge.
"""

import json
import types
from pathlib import Path

import pytest

from app.planning import staged_outline as so
from evals.offline import corpus_canon
from evals.steps.pipeline import load_corpus

pytestmark = pytest.mark.unit

_SKELETON = json.dumps(
    {
        "premise_restated": "A story.",
        "central_conflict": "A conflict.",
        "acts": [{"title": "Act 1", "goal": "g", "turn": "t", "chapters_planned": 2}],
    }
)
_CHAPTERS = json.dumps(
    [{"title": "One", "summary": "s", "act_index": 0, "pov": "", "people": []}]
)
_ELARA = json.dumps(
    [
        {
            "title": "The Chosen One",
            "summary": "Elara of Corporeal Energy Logistics rises.",
            "children": [{"title": "Elara rebels", "summary": "x", "children": []}],
        }
    ]
)
_FAITHFUL = json.dumps(
    [
        {
            "title": "Onboarding",
            "summary": "Milo Voss is retained; Zara Okafor recruits him; "
            "Edric Thane, the Chief Executive Lich, watches.",
            "children": [],
        }
    ]
)


class FakeLLM:
    def __init__(self, beats: str):
        self.beats = beats
        self.calls: list[str] = []

    async def generate(self, messages, *, purpose, **kw):
        self.calls.append(purpose)
        text = {
            "outline:skeleton": _SKELETON,
            "outline:chapters": _CHAPTERS,
            "outline:beats": self.beats,
        }[purpose]
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)


@pytest.fixture
def cel_canon():
    _text, gt = load_corpus("cel")
    return corpus_canon(gt)


async def test_elara_outline_fails_gate_repairs_once_and_saves(monkeypatch, cel_canon):
    fake = FakeLLM(_ELARA)
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    nodes, warnings = await so.generate_staged_outline(cel_canon, chapters_target=6)
    assert fake.calls.count("outline:chapters") == 2  # exactly one repair
    assert nodes, "an expensive job is never hard-failed on a heuristic"
    assert any("missing principal" in w for w in warnings)
    for name in ("Milo Voss", "Zara Okafor", "Edric Thane"):
        assert name in " ".join(warnings)


async def test_faithful_outline_passes_without_repair(monkeypatch, cel_canon):
    fake = FakeLLM(_FAITHFUL)
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    _nodes, warnings = await so.generate_staged_outline(cel_canon, chapters_target=6)
    assert fake.calls.count("outline:chapters") == 1
    assert not any("missing principal" in w for w in warnings)


def test_cel_corpus_still_exercises_the_historical_cap(cel_canon):
    """The corpus must keep being able to see the bug: synopsis past the old
    2,000-char cap, and the cast first appearing past char 4,000."""
    syn = cel_canon.synopsis
    assert len(syn) > 2000
    assert syn.find("Milo") > 2000


# --- Invented-names warning: quiet on ordinary prose (SPEC-21) ---------------

# Verbatim beats from the baseline relay outline whose warning listed 'Desk',
# 'Following', 'Debt In', 'Confetti That Dissolves At' and 'Meeting That Could
# Have Been' as invented names (orchestrator finding #1, unknown_rate 0.276).
_BASELINE_BEATS = [
    {
        "title": "A Meeting That Could Have Been an Email",
        "summary": "Milo dies mid-sentence complaining about a pointless meeting, "
        "and the life he thought of as comfortable is revealed in a single breath "
        "to have been forty-one years of competent boredom.",
    },
    {
        "title": "The Intake Atrium",
        "summary": "A caseworker slides a laminated org chart across a laminate "
        "desk and tells Milo he has been retained as a strong culture fit.",
    },
    {
        "title": "Lanyard, Desk, Mentor",
        "summary": "Milo receives his lanyard and is seated beside Zara Okafor in "
        "the windowless Marrow District; when he asks which department the CEL "
        "is, she does not correct him.",
    },
    {
        "title": "Confetti That Dissolves",
        "summary": "At the all-hands in the bedrock amphitheater one soul "
        "graduates to applause and confetti that vanishes before it lands.",
    },
    {
        "title": "The Third Son's Debt",
        "summary": "In memory, Edric dies a minor merchant house's clerk owing a "
        "debt he cannot pay, is retained, and decides the only way out of a "
        "machine is to own it.",
    },
    {
        "title": "Following the Flow",
        "summary": "Milo traces every discrepancy to one pooled account at the "
        "top of the org chart and understands that the souls sent under are "
        "harvested to keep the CEL continuing.",
    },
    {
        "title": "Closing the Account",
        "summary": "Under Zara's precise training Milo learns the vocabulary of "
        "tenure, quotas and billable hauntings at Aeon Holdings.",
    },
]
_BASELINE_NOISE = {
    "Desk",
    "Following",
    "Debt In",
    "Confetti That Dissolves At",
    "Meeting That Could Have Been",
}


def _audit(cel_canon, nodes, *, with_canon_text=True):
    from app.planning.fidelity import audit_outline, canon_known_text

    return audit_outline(
        nodes,
        cel_canon.principals(),
        cel_canon.canon_terms(),
        cast=so._cast_index(cel_canon),
        canon_text=canon_known_text(cel_canon) if with_canon_text else "",
    )


def _chapter(*beats):
    return [
        {
            "title": "Retained",
            "summary": "Milo Voss is retained; Zara Okafor recruits him; "
            "Edric Thane watches.",
            "children": [dict(b, children=[]) for b in beats],
        }
    ]


def _flagged(audit) -> set[str]:
    return {" ".join(u.split()) for u in audit.unknown}


def test_baseline_outline_words_are_not_flagged(cel_canon):
    audit = _audit(cel_canon, _chapter(*_BASELINE_BEATS), with_canon_text=False)
    flagged = _flagged(audit)
    assert not (flagged & _BASELINE_NOISE)
    assert not (flagged & {"Lanyard", "Closing", "Under", "Intake Atrium", "Son"})


def test_names_the_canon_text_already_uses_are_known(cel_canon):
    """Aeon Holdings, the Marrow District and the CEL are synopsis names."""
    bare = _audit(cel_canon, _chapter(*_BASELINE_BEATS), with_canon_text=False)
    assert "Marrow District" in bare.unknown  # only the synopsis knows it
    audit = _audit(cel_canon, _chapter(*_BASELINE_BEATS))
    assert audit.unknown == []
    assert not any("may be inventions" in w for w in audit.warnings)


def test_a_truly_invented_name_is_still_flagged(cel_canon):
    beat = {
        "title": "A New Hire",
        "summary": "Milo shares a desk with Elara Moonwhisper, who files "
        "nothing. Elara Moonwhisper smiles.",
    }
    audit = _audit(cel_canon, _chapter(*_BASELINE_BEATS, beat))
    assert audit.unknown == ["Elara Moonwhisper"]
    assert any("Elara Moonwhisper" in w for w in audit.warnings)
    assert audit.passed, "the invented-names check stays a warning, never a gate"


def test_a_capitalised_run_never_spans_a_field_boundary():
    from app.planning.fidelity import unknown_proper_nouns

    # flatten_outline joins fields with two spaces: "Debt" closes one field
    # and "In" opens the next, so they are never one phrase.
    flagged = unknown_proper_nouns("He meets Harlan Crane  In memory, he paid.", set())
    assert flagged == ["Harlan Crane"]


def test_a_name_only_in_a_title_is_not_reported_but_one_in_prose_is(cel_canon):
    titled = {"title": "Ballad of Quillon", "summary": "Milo files a memo."}
    assert "Quillon" not in _audit(cel_canon, _chapter(titled)).unknown
    prose = {"title": "A Memo", "summary": "Milo files a memo for Quillon."}
    assert _audit(cel_canon, _chapter(prose)).unknown == ["Quillon"]


def test_title_case_fragments_and_sentence_openers_are_not_names():
    from app.planning.fidelity import unknown_proper_nouns

    text = (
        "Following orders, Milo signs. He attends the Meeting That Could Have "
        "Been An Email. Suddenly, Brannoch Vale arrives."
    )
    assert unknown_proper_nouns(text, {"Milo"}) == ["Brannoch Vale"]


# NB-1: the reviewer's probe. A one-word capital that only ever opens a
# sentence is not a name; a two-word name, or one used mid-sentence, is.
PROBE_DRAFT = (
    "Milo walked into the office. The caseworker looked up from her desk and sighed."
)
PROBE_QUIET = [
    "Silence fell. Milo crossed the room.",
    "Rain hammered the glass. Milo waited.",
    "Somewhere a phone rang. Nobody answered.",
    "Dust hung in the air. Perhaps Milo was wrong.",
    "Outside, Milo paused.",
    '"Fine," Milo said. "Whatever."',
    "'Don't explain.'",  # W7-5 (a): a contraction's stem is not a name
    "'Won't'",
    "'Can't'",
    "Milo said Quince’ll file it, and Quince’d know.",  # curly, 'll, 'd
]
PROBE_FLAGGED = {
    "Milo nodded. Bartholomew Quince smiled.": ["Bartholomew Quince"],
    "Milo nodded to Quince.": ["Quince"],
    "Milo sat at Gary's desk.": ["Gary"],  # a possessive still names someone
    "Milo sat at Quince’s desk.": ["Quince"],
}


@pytest.mark.parametrize("text", PROBE_QUIET)
def test_a_word_that_only_opens_sentences_is_not_a_name(text):
    from app.planning.fidelity import unknown_proper_nouns

    assert unknown_proper_nouns(text, {"Milo"}, canon_text=PROBE_DRAFT) == []


@pytest.mark.parametrize("text, flagged", PROBE_FLAGGED.items())
def test_a_two_word_or_mid_sentence_name_is_still_reported(text, flagged):
    from app.planning.fidelity import unknown_proper_nouns

    assert unknown_proper_nouns(text, {"Milo"}, canon_text=PROBE_DRAFT) == flagged


def test_the_summoned_upstairs_warning_has_no_contraction(cel_canon):
    """(F) SPEC-57: W6b's plan line no longer warns of "Don"."""
    path = Path(__file__).parents[1] / "fixtures" / "w6_archives.json"
    title, summary = json.loads(path.read_text())["summoned_upstairs"]["line"].split(
        ": ", 1
    )
    assert (
        _audit(cel_canon, _chapter({"title": title, "summary": summary})).unknown == []
    )


def test_a_name_that_only_opens_an_outline_sentence_stays_quiet(cel_canon):
    beat = {"title": "Arrival", "summary": "Prava arrives. Milo files a memo."}
    assert _audit(cel_canon, _chapter(beat)).unknown == []


def test_principal_recall_finds_a_punctuated_alias():
    """PR-S2: recall and classify_people share names.norm / names.mentions,
    so "Voss," counts as a mention in both."""
    from app.planning.fidelity import principal_recall

    recall, present, missing = principal_recall(
        "Then Voss, alone, signs.", [{"name": "Milo Voss", "aliases": ["Voss,"]}]
    )
    assert recall == 1.0 and present == ["Milo Voss"] and missing == []


def test_an_all_caps_sign_is_not_a_name():
    """N10 (SPEC-33): run-W25's signs and stamps are never reported; a real
    invented name beside them still is (the NB-1 probe)."""
    from app.planning.fidelity import unknown_proper_nouns

    text = (
        "Milo initialled the box marked INITIAL HERE under NOW SERVING 4,118,206. "
        "His mug said I SURVIVED Q3. The last page said SEE APPENDIX M, and the "
        "stamp read DISPOSITION PENDING. Milo nodded. Bartholomew Quince smiled."
    )
    assert unknown_proper_nouns(text, {"Milo"}, canon_text=PROBE_DRAFT) == [
        "Bartholomew Quince"
    ]


# Run-W3b's prose (eval-report-cel.json, prose and revise): labels and a
# slide title were reported as invented names (M5).
W3B = (
    "In his last job his manager had spent forty minutes on a slide called Our "
    "Values, and here was a person who had boiled the whole role down to two "
    "sentences. He peeled back the first page of Appendix A, and the print was "
    "smaller than he had expected. He read Appendix A as far as page three, then "
    "the finger on Appendix F. He filed Form 9 at Desk R-14. 'The balance thereof "
    "transferred to the Consolidated Continuance Pool for the benefit of' it read. "
    "Milo nodded. Bartholomew Quince smiled."
)


def test_labels_and_a_title_after_our_are_not_names():
    """M5: a capital followed by a label token (a single capital, a number or
    a code) is a label, and "Our" opens a title; invented names still report."""
    from app.planning.fidelity import unknown_proper_nouns

    assert unknown_proper_nouns(W3B, {"Milo"}, canon_text=PROBE_DRAFT) == [
        "Bartholomew Quince",
        "Consolidated Continuance Pool",
    ]
    # (F) NB-1: a mid-sentence "The" before a longer name is trimmed, not kept.
    mid = (
        "In the morning, The Consolidated Continuance Pool opened. "
        "He went to the market, then to The Marrow Exchange."
    )
    assert unknown_proper_nouns(mid, set(), canon_text="milo zara") == [
        "Consolidated Continuance Pool",
        "Marrow Exchange",
    ]


# W5-5 (SPEC-44): one rule for honorifics, calendar plurals and joined names,
# in a canon that never writes "Mr" (rows measured before the change).
NAME_RULE = [
    ('"Welcome aboard, Mr. Voss," she said.', []),  # (F) was ['Mr']
    ("Then Mr. Quince arrived.", ["Mr. Quince"]),  # (F) was ['Mr']
    ("He filed the forms on Fridays.", []),  # (F) was ['Fridays']
    ("He read Monday's memo twice.", []),  # (R)
    # (F) W4b2; was ['Central Escrow', 'Ledgers']
    (
        "He went down to Central Escrow and Ledgers with the form.",
        ["Central Escrow and Ledgers"],
    ),
    # (F) was ['Department', 'Records']
    ("He walked to the Department of Records at noon.", ["Department of Records"]),
    ("Prava and Bartholomew Quince waited by the desk.", ["Bartholomew Quince"]),
    ("Milo and Zara said nothing.", []),  # (R)
    (  # (R) NB-1
        "In the morning, The Consolidated Continuance Pool opened.",
        ["Consolidated Continuance Pool"],
    ),
    # (F) a whole-word title never joins across a period; was []
    ("She bowed to the Count. The Marrow Exchange was shut.", ["Marrow Exchange"]),
    ("He thanked the Lord. Then Quince left.", ["Quince"]),  # (F) was []
]


@pytest.mark.parametrize("text, flagged", NAME_RULE)
def test_names_not_honorifics_weekdays_or_split_phrases(text, flagged):
    from app.planning.fidelity import unknown_proper_nouns

    known = {"Milo Voss", "Zara", "Prava"}
    assert unknown_proper_nouns(text, known, canon_text=PROBE_DRAFT) == flagged


def test_the_audit_names_each_scene_its_pov_cannot_witness():
    """(F) W6-4 (b), D9: each wave-5 outline warns on its leaky scene; (R) W4, W4b2."""
    from app.planning.fidelity import POV_LEAK_WARNING, audit_outline

    fixtures = Path(__file__).parents[1] / "fixtures"
    w5 = json.loads((fixtures / "w5_archives.json").read_text())
    outlines = json.loads((fixtures / "w4_outlines.json").read_text())
    for row, leak in ((r["run"], r["leak"]) for r in w5["outline_audit"]["rows"]):
        chapters: dict = {}
        for scene in outlines.get(row) or w5["outlines"][row]:
            chapters.setdefault(scene["chapter"], []).append(scene)
        nodes = [{"children": scenes} for scenes in chapters.values()]
        found = [w for w in audit_outline(nodes, [], set()).warnings if '" ("' in w]
        expected = leak and 'chapter {chapter} "{scene}" ("{phrase}")'.format(**leak)
        assert found == ([POV_LEAK_WARNING + expected] if leak else []), row
