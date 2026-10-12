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
    [{"title": "One", "summary": "s", "act_index": 0, "pov": "", "characters": []}]
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
