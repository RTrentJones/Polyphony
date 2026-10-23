"""W13-4 (SPEC-84's panel amendment): the model panel and score's rated path."""

import json
import re
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from evals.baseline import panel, score
from evals.harness.cache import Cache

pytestmark = pytest.mark.unit
WF = yaml.safe_load(Path(".github/workflows/baseline.yml").read_text())


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


async def test_an_unparseable_panel_reply_is_never_memoised(monkeypatch, tmp_path):
    # (F) W15-9: three blank replies leave the item unrated with no cache file;
    # the resume asks again, stores the parseable reply and rates it.
    replies = ["", "", "", '{"errors": 1}']
    llm = SimpleNamespace(
        generate=AsyncMock(side_effect=[SimpleNamespace(text=t) for t in replies])
    )
    monkeypatch.setattr(panel, "LLMClient", lambda provider: llm)
    b, cache = {"items": {"x": "X."}, "pairs": [], "canon": "<canon/>"}, Cache(
        str(tmp_path), "sha"
    )
    assert await panel.rate("stub:m", [b], cache) == {"prefer": {}, "errors": {}}
    assert list(tmp_path.rglob("panel-*.json")) == []
    assert await panel.rate("stub:m", [b], cache) == {"prefer": {}, "errors": {"x": 1}}
    assert len(list(tmp_path.rglob("panel-*.json"))) == 1


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
    with pytest.raises(SystemExit, match="no bundle to rate"):
        await panel.main(str(tmp_path), tmp_path)
    (tmp_path / "bundle.json").touch()
    await panel.main(str(tmp_path), tmp_path)
    assert json.loads((tmp_path / "kappa.json").read_text())["unrated"] == 3
    key = tmp_path / "key.json"  # a second slot (z1) the ratings never reached
    key.write_text(json.dumps({"items": {i: [s, i] for i, s in ["x0", "y0", "z1"]}}))
    monkeypatch.setattr("sys.argv", ["score", str(tmp_path), str(key)])
    with pytest.raises(SystemExit, match="panel incomplete: re-run the rate dispatch"):
        runpy.run_module("evals.baseline.score", run_name="__main__")
    (tmp_path / "kappa.json").write_text('{"unrated": 0, "fleiss_kappa": 1}')
    with pytest.raises(SystemExit, match="ratings do not cover the key's slots"):
        runpy.run_module("evals.baseline.score", run_name="__main__")


@pytest.mark.parametrize("kind, code", [("key", 1), ("state", 1), ("bundle", 0)])
def test_the_rate_job_gets_bundles_only_and_its_guard_fails(tmp_path, kind, code):
    rate, ok = WF["jobs"]["rate"], r"-p 'baseline-bundle-\*'|-n baseline-rate-state "
    runs = "".join(s.get("run", "") for s in rate["steps"])
    gets = re.findall(r"gh run download .*", runs)
    assert gets and all(re.search(ok, g) for g in gets) and "GH_TOKEN" not in WF["env"]
    assert not re.search(r"baseline-(key|state)-|LLM_MAX_RPD", f"{rate}{WF['env']}")
    (tmp_path / f"bundles/1/baseline-{kind}-cel").mkdir(parents=True)
    g = next(s["run"] for s in rate["steps"] if s.get("name") == "Leak guard")
    assert subprocess.run(["bash", "-ec", g], cwd=tmp_path).returncode == code
