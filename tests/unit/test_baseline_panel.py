"""W13-4 (SPEC-84's panel amendment): the model panel and score's rated path."""

import json
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals.baseline import panel, score
from evals.harness.cache import Cache
from evals.stub_llm import _answer

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


async def test_the_stub_panel_is_order_consistent(monkeypatch, tmp_path):
    say = lambda m, **k: SimpleNamespace(text=_answer(m[0]["content"]))  # noqa: E731
    llm = SimpleNamespace(generate=AsyncMock(side_effect=say))
    monkeypatch.setattr(panel, "LLMClient", lambda provider: llm)
    b = {"items": {"x": "A.", "y": "B c."}, "pairs": ["xy"], "canon": "<canon/>"}
    got = await panel.rate("stub:m", [b], Cache(str(tmp_path), "sha"))
    assert got == {"prefer": {("x", "y"): "y"}, "errors": {"x": 0, "y": 0}}


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


async def test_a_partial_panel_records_unrated_and_score_refuses(monkeypatch, tmp_path):
    cut = [{"prefer": {"xy": "x"}, "errors": {"x": 0}}, {"prefer": {}, "errors": {}}]
    monkeypatch.setattr(panel, "rate", AsyncMock(side_effect=cut))  # b hit its quota
    monkeypatch.setattr(panel, "load", lambda p: {"items": "xy", "pairs": ["xy"]})
    monkeypatch.setenv("BASELINE_PANEL", "a:m,b:m")
    monkeypatch.setenv("EVAL_CACHE_DIR", str(tmp_path))
    (tmp_path / "bundle.json").touch()
    await panel.main(str(tmp_path), tmp_path)
    assert json.loads((tmp_path / "kappa.json").read_text())["unrated"] == 3
    monkeypatch.setattr("sys.argv", ["score", str(tmp_path)])
    with pytest.raises(SystemExit, match="panel incomplete: re-run the rate dispatch"):
        runpy.run_module("evals.baseline.score", run_name="__main__")
