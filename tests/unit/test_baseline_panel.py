"""W13-4 (SPEC-84's panel amendment): the model panel and score's rated path."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.baseline import panel, score
from evals.harness.cache import Cache

pytestmark = pytest.mark.unit


def test_a_bundle_with_an_arm_marker_is_refused(tmp_path):
    p = tmp_path / "baseline-bundle-cel" / "bundle.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"items": {"i1": "Beat 1: x"}, "pairs": [], "edit": []}))
    with pytest.raises(SystemExit, match="not a blinded bundle"):
        panel.load(p)


async def test_an_order_inconsistent_member_scores_a_tie(monkeypatch, tmp_path):
    answer = SimpleNamespace(text='{"preferred": 1, "errors": 2}')  # always first
    llm = SimpleNamespace(generate=AsyncMock(return_value=answer))
    monkeypatch.setattr(panel, "LLMClient", lambda provider: llm)
    b = {"items": {"x": "X.", "y": "Y."}, "pairs": [["x", "y"]], "canon": "<canon/>"}
    got = await panel.rate("stub:m", [b], Cache(str(tmp_path), "sha"))
    assert got == {"prefer": {("x", "y"): "tie"}, "errors": {"x": 2, "y": 2}}


def test_fleiss_kappa_on_a_hand_computed_table():
    # p = (3, 4, 1) / 8, so Pe = 13/32; P = (1 + 1 + 0 + 0) / 4; (1/2 - 13/32) / (19/32)
    table = [[2, 0, 0], [0, 2, 0], [1, 1, 0], [0, 1, 1]]
    assert panel.fleiss(table) == pytest.approx(3 / 19)


def test_a_panel_sheet_gives_continuity_per_scene_and_low_kappa_no_decision(tmp_path):
    key = {"items": {"i1": ["cel-1", "A"], "i2": ["cel-1", "B"]}}
    panel.write(tmp_path / "s.csv", {("i1", "i2"): "i2"}, {"i1": 1.5, "i2": 0.0})
    judgments, cont = score.rated(tmp_path / "s.csv", key)
    assert judgments == [("cel-1", "A", "B", "B")]
    record = {"slot": "cel-1", "latency_ms": 1, "requests": 1, "unpriced_tokens": 0}
    out = score.score([record | {"arm": a} for a in "AB"], judgments, cont, 3 / 19)
    assert out["decision"] == "no decision (panel κ < 0.2)"
    assert [out[a]["continuity_per_scene"] for a in "AB"] == [1.5, 0.0]
