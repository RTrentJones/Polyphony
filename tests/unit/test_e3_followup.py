"""SPEC-95 E3: the follow-up runner on P⁵'s frozen panel. No LLM call."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evals.baseline import panel
from evals.followup import e3
from evals.harness.cache import Cache

ROOT = Path(__file__).resolve().parents[2]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "e3-followup.yml").read_text())


def test_the_frozen_blobs_are_p5s_and_a_changed_file_is_refused(tmp_path):
    assert e3.check_frozen() == e3.FROZEN
    for p in e3.FROZEN:  # the ids are git's own blob ids
        assert (
            e3.blob_id(ROOT / p)
            == subprocess.run(
                ["git", "hash-object", str(ROOT / p)], capture_output=True, text=True
            ).stdout.strip()
        )
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / p, tmp_path / p)
    with (tmp_path / "evals/baseline/panel.py").open("a") as f:
        f.write("\n")
    with pytest.raises(SystemExit, match="panel.py"):
        e3.check_frozen(tmp_path)


def _bundle(root: Path, text: str = "X.") -> Path:
    d = root / "bundles" / "1" / "baseline-bundle-cel"
    d.mkdir(parents=True)
    b = {"items": {"x": text, "y": "Y."}, "pairs": [["x", "y"]], "edit": []}
    (d / "bundle.json").write_text(json.dumps(b))
    return root / "bundles"


class _Live:
    """Prefers the passage holding "Y."; one refused reply on the first call."""

    calls = 0

    def __init__(self, provider):
        self.provider = provider

    async def generate(self, messages, **kw):
        from app.llm.errors import ProviderRefusalError

        _Live.calls += 1
        if _Live.calls == 1:
            raise ProviderRefusalError("refused", spent=("", 100, 0, 0.0002))
        text = messages[0]["content"]
        if "preferred" in text[-120:]:
            second = text.split("<passage_2>")[1]
            ans = '{"preferred": 2}' if "Y." in second else '{"preferred": 1}'
        else:
            ans = '{"errors": 1}'
        return SimpleNamespace(text=ans, tokens_in=1000, tokens_out=10, cost_usd=0.0021)


async def _seed_w12_9(cache_dir: str, bundles: Path) -> None:
    """W12-9's rate-state: the free members' answers under the frozen keys."""
    answer = SimpleNamespace(text='{"preferred": 1, "errors": 0}')

    class Old:
        def __init__(self, provider):
            pass

        async def generate(self, *a, **kw):
            return answer

    old = panel.LLMClient
    panel.LLMClient = Old
    try:
        b = [panel.load(p) for p in bundles.rglob("bundle.json")]
        for m in e3.REPLAYED:
            await panel.rate(m, b, Cache(cache_dir, "panel"))
    finally:
        panel.LLMClient = old


async def test_rate_runs_sonnet_live_replays_w12_9_and_retests(tmp_path, monkeypatch):
    import app.llm.client as client

    bundles = _bundle(tmp_path)
    cache = str(tmp_path / "rate-state" / "cache")
    await _seed_w12_9(cache, bundles)
    monkeypatch.setenv("EVAL_CACHE_DIR", cache)
    monkeypatch.setattr(client, "LLMClient", _Live)
    _Live.calls = 0
    rec = await e3.rate(bundles, tmp_path / "out", retest=True)
    # 4 prompts per pass (one pair in two orders, two continuity items); pass
    # 1's refused reply was re-asked (1 + 4), the retest asked all 4 again under
    # its own cache (pass 1's answers not replayed)
    assert rec["usage_usd"]["pass1"]["calls"] == 5
    assert rec["usage_usd"]["pass1"]["unusable"] == 1
    assert rec["usage_usd"]["retest"]["calls"] == 4
    assert rec["sonnet_cost_usd"] == pytest.approx(0.0002 + 8 * 0.0021)
    assert rec["unrated"] == dict.fromkeys(e3.SHEETS, 0)
    out = tmp_path / "out"
    assert {p.name for p in out.iterdir()} >= set(e3.SHEETS.values()) | {"e3.json"}
    sonnet = (out / "sheet-sonnet.csv").read_text()
    assert "x,y,y," in sonnet  # Y preferred in both orders
    assert "x,y,tie," in (out / "sheet-groq.csv").read_text()  # replayed: first
    assert (tmp_path / "rate-state/cache/panel-retest").is_dir()


async def test_a_prompt_w12_9_never_asked_fails_instead_of_calling(
    tmp_path, monkeypatch
):
    import app.llm.client as client

    seeded = _bundle(tmp_path / "w12-9")
    cache = str(tmp_path / "cache")
    await _seed_w12_9(cache, seeded)
    monkeypatch.setenv("EVAL_CACHE_DIR", cache)
    monkeypatch.setattr(client, "LLMClient", _Live)
    _Live.calls = 1  # no refusal this time
    changed = _bundle(tmp_path / "now", text="X, changed.")
    with pytest.raises(e3.CacheMiss, match="not byte-identical"):
        await e3.rate(changed, tmp_path / "out", retest=False)
    assert panel.LLMClient is not _Live and "LLMClient" in dir(panel)  # restored


def _key(n: int = 18) -> dict:
    return {"items": {f"{arm}{k}": [f"s{k}", arm] for k in range(n) for arm in "ABC"}}


def _sheet(path: Path, winner, n: int = 18, errors=None) -> None:
    prefer = {}
    for k in range(n):
        for x, y in (("A", "B"), ("A", "C"), ("B", "C")):
            prefer[(f"{x}{k}", f"{y}{k}")] = winner(k, x, y)
    errs = errors or {
        f"{arm}{k}": {"A": 1, "B": 0, "C": 2}[arm] for k in range(n) for arm in "ABC"
    }
    panel.write(path, prefer, errs)


def _ratings(tmp_path, sonnet, retest=None) -> Path:
    r = tmp_path / "ratings"
    r.mkdir()
    _sheet(r / "sheet-sonnet.csv", sonnet)
    _sheet(r / "sheet-groq.csv", lambda k, x, y: "tie")
    _sheet(r / "sheet-nemotron.csv", lambda k, x, y: f"{y}{k}" if k % 2 else "tie")
    _sheet(r / "sheet-panel-sonnet-nemotron.csv", lambda k, x, y: "tie")
    if retest:
        _sheet(r / "sheet-sonnet-retest.csv", retest)
    meta = {"unrated": dict.fromkeys(e3.SHEETS, 0), "sonnet_cost_usd": 1.23}
    (r / "e3.json").write_text(json.dumps(meta))
    return r


def test_score_primary_at_95_with_975_beside_and_kappas(tmp_path):
    b_wins = lambda k, x, y: f"{y}{k}" if (x, y) == ("A", "B") else "tie"  # noqa: E731
    out = e3.score(_ratings(tmp_path, b_wins, retest=b_wins), _key())
    assert out["P(B>A) sonnet 95%"]["p"] == 1.0
    assert out["decision"].startswith("Sonnet prefers B to A")
    assert "W12-9's decision unchanged" in out["decision"]
    assert out["AB order flips (sonnet)"] == "0/18"
    assert out["order consistency"][e3.GROQ]["AB"] == "0/18"
    assert out["continuity A-B (sonnet)"]["diff"] == pytest.approx(1.0)
    assert (
        out["retest (sonnet pass 1 vs 2), descriptive"]["same pair outcome"] == "54/54"
    )
    assert out["panel sonnet+nemotron, descriptive"]["fleiss_kappa"]["AB"] is not None
    assert out["sonnet_cost_usd"] == 1.23


def test_score_order_driven_sonnet_is_undetermined(tmp_path):
    flips = lambda k, x, y: "tie" if k < 10 else f"{y}{k}"  # noqa: E731
    out = e3.score(_ratings(tmp_path, flips), _key())
    assert out["AB order flips (sonnet)"] == "10/18"
    assert out["decision"].startswith("undetermined: Sonnet is order-driven")


def test_score_refuses_unrated_sonnet(tmp_path):
    r = _ratings(tmp_path, lambda k, x, y: "tie")
    meta = json.loads((r / "e3.json").read_text())
    meta["unrated"][e3.SONNET] = 2
    (r / "e3.json").write_text(json.dumps(meta))
    with pytest.raises(SystemExit, match="unrated"):
        e3.score(r, _key())


def test_the_workflow_rates_bundles_only_with_one_key_and_a_cap(tmp_path):
    rate, score = WF["jobs"]["rate"], WF["jobs"]["score"]
    keys = {k for k in rate["env"] if k.endswith("_API_KEY")}
    assert keys == {"ANTHROPIC_API_KEY"}  # Groq and Nemotron are replayed
    runs = "\n".join(s.get("run", "") for s in rate["steps"])
    assert "baseline-key" not in runs and "records" not in runs
    assert "baseline-rate-state" in runs and "W12_9_RATE_RUN" in runs
    assert WF["env"]["W12_9_RATE_RUN"] == "40248080586"
    assert "baseline-key-*" in "\n".join(s.get("run", "") for s in score["steps"])
    for job in (rate, score):
        for s in job["steps"]:
            assert "${{" not in s.get("run", "")  # inputs via env only
            uses = s.get("uses", "")
            assert (
                not uses
                or uses.startswith("./")
                or "@" in uses
                and len(uses.split("@")[1]) == 40
            )
    cap = next(s for s in rate["steps"] if s.get("name") == "The run's Claude ceiling")
    for value, code in (("5", 0), ("0", 1), ("", 1), ("5;x", 1)):
        genv = tmp_path / f"env{code}{value}"
        out = subprocess.run(
            ["bash", "-c", cap["run"]],
            env={
                "PATH": "/usr/bin:/bin",
                "CAP": value,
                "ANTHROPIC_API_KEY": "k",
                "GITHUB_ENV": str(genv),
            },
            capture_output=True,
            text=True,
        )
        assert out.returncode == code, (value, out.stdout)
        if code == 0:
            assert genv.read_text() == "MONTHLY_COST_CEILING_USD=5\n"
