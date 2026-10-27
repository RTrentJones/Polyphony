"""Unit tests for evals.run helpers — quota short-circuit classification."""

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl

import httpx
import pytest

from evals.harness.client import PolyphonyClient
from evals.run import _is_quota_error

pytestmark = pytest.mark.unit


def test_quota_error_matches_gemini_429():
    msg = (
        "EvalClientError: test-dialogue failed (500): "
        '{"error_type":"RateLimitError","error_detail":"Error code: 429 - '
        'You exceeded your current quota, please check your plan and billing"}'
    )
    assert _is_quota_error(msg) is True


def test_quota_error_matches_bare_ratelimit():
    assert _is_quota_error("RateLimitError: 429 quota exceeded") is True


def test_non_quota_error_is_ignored():
    # A 500 that is NOT a quota/429 must not trip the short-circuit.
    assert _is_quota_error("EvalClientError: outline failed (500): boom") is False
    assert _is_quota_error("TimeoutError: read timed out") is False
    # A 429 with no quota signal (e.g. a stray status code in prose) stays false.
    assert _is_quota_error("got 429 responses in the sample text") is False


def test_preflight_declares_exactly_five_runs():
    """(F) Wave-9 day-0 (SPEC-68): each run's exact --steps, fresh flag and
    minimum daily limit (re-measured at wave-10 day-0, SPEC-75; baseline-dev at
    wave-12 day-0); every run's step is a registered one (W12-5 adds baseline)."""
    from evals.preflight import RUNS
    from evals.run import all_steps

    full = "extraction,ingestion,retrieval,outline,continuity,prose,revise"
    got = {n: (",".join(r.steps), r.fresh, r.min_daily_tokens) for n, r in RUNS.items()}
    assert got == {
        "gated-full": (full, True, 340_000),
        "gated-scenes": ("outline,prose,revise", True, 320_000),
        "attribution": ("attribution", True, 40_000),
        "baseline-dev": ("baseline", True, 630_000),
        "baseline": ("baseline", False, 750_000),
    }
    assert {s for r in RUNS.values() for s in r.steps} <= set(all_steps())


def test_both_draws_exempt_is_not_exercised(tmp_path):
    """(F) W10-3 (c): an exemptible check must run (n > 0) in one of the two
    gated draws; both exempt fails as "not exercised", and the CLI exits 1."""
    from evals.gate import exercised, main

    shc = [
        {"steps": {"prompt_contract": {"secondary_has_context": {"n": n}}}}
        for n in (0, 0, 3)
    ]
    assert exercised(shc[:2]) == ["secondary_has_context: not exercised"]
    assert exercised(shc[1:]) == []
    (path := tmp_path / "r.json").write_text(json.dumps(shc[0]))
    assert main(["exercised", str(path), str(path)]) == 1
    (ok := tmp_path / "ok.json").write_text(json.dumps(shc[2]))
    assert main(["exercised", str(ok)]) == 0  # one gated draw suffices (SPEC-75)


@pytest.mark.parametrize("a", [{"error": "boom"}, {"skipped": True}])
def test_main_exits_1_on_a_step_error_and_0_on_skips(monkeypatch, a):
    """(F) a step error exits 1 (W13-7); (R) skips and passes exit 0."""
    import evals.run as r

    steps = {"steps": {"a": a, "b": {"score": 1.0}}}
    monkeypatch.setattr(r, "run", AsyncMock(return_value=steps))
    monkeypatch.setattr(r.report, "scorecard", lambda res: "")
    monkeypatch.setattr(r.report, "history", lambda h: "")
    monkeypatch.setattr("sys.argv", ["run"])  # --steps all: never `baseline`
    red = "error" in a
    with pytest.raises(SystemExit, match="1") if red else contextlib.nullcontext():
        r.main()
    assert "baseline" not in r.run.await_args[0][1]
    assert "rnrc_validation" not in r.run.await_args[0][1]
    assert "rn2_validation" not in r.run.await_args[0][1]
    assert "calibration_answers" not in r.run.await_args[0][1]


async def test_a_baseline_run_resumes_as_its_one_user_and_skips_the_contract(
    tmp_path, monkeypatch
):
    """(F) run-W13dry: a resumed dispatch logs in as the user the restored DB
    owns, and no prompt contract is graded over the recorder's archive."""
    import evals.run as r
    from tests.unit.test_evals_preflight import _cfg, _client

    users = {"a@x": "pw"}

    def app(req):  # login is a form, register and invites are JSON
        b = dict(parse_qsl(req.content.decode())) or json.loads(req.content)
        ok = users.get(b.get("username")) == b.get("password")
        if "email" in b:
            assert b["email"] not in users  # registered once, then logged into
            users[b["email"]] = b["password"]
        return httpx.Response(
            401 if "username" in b and not ok else 200,
            json={"access_token": "t", "code": "c"},
        )

    async def bootstrap(*args):  # a fresh runner per dispatch, the same DB
        c = PolyphonyClient("http://app")
        c._http = httpx.AsyncClient(
            base_url="http://app", transport=httpx.MockTransport(app)
        )
        return await c.bootstrap_eval_user(*args)

    cfg, judge = _cfg(tmp_path), SimpleNamespace(provider_id="r", is_self=False)
    judge.model_override = judge.fell_back = judge.endpoint = None
    fake = _client(10**7)
    fake.bootstrap_eval_user = bootstrap
    monkeypatch.setattr(r, "load_config", lambda: cfg)
    monkeypatch.setattr(r, "Judge", lambda _cfg: judge)
    monkeypatch.setattr(r, "PolyphonyClient", lambda url: fake)
    monkeypatch.setattr(r.get_step("baseline"), "run", AsyncMock(return_value={}))
    contract = AsyncMock(side_effect=ValueError("no drawn chapter"))
    monkeypatch.setattr(r.pipeline, "prompt_contract_result", contract)
    for _ in range(2):
        out = await r.run("cel", ["baseline"], str(tmp_path / "r.json"), 1, "baseline")
    assert "prompt_contract" not in out["steps"]
    assert list(users) == ["a@x", "eval-baseline@polyphony.eval"]


def test_month_spend_counts_each_runs_max_report_and_missing_usage_as_zero(
    tmp_path, capsys, monkeypatch
):
    # (F) W15-0: a run's book reports each hold the run DB's cumulative spend,
    # so the run counts its max (not the sum); a report without usage counts 0,
    # an external-mode report never counts, and a run killed before any usage
    # (only its eval-ceiling.json) counts at its ceiling.
    from evals import spend

    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    usage = {"usage": {"deployment_cost_30d_usd": 9.0}}
    runs = {  # month_cost_usd is one book's user (run 40030061261: 2.52 of 5.66)
        "1/a1": [
            {"usage": {"deployment_cost_30d_usd": c, "month_cost_usd": 0.6}}
            for c in (0.8, 2.1, 2.6)
        ],
        "2/a1": [{}, usage | {"mode": "external"}],
        "2/a2": [{"ceiling": 4.0}],  # killed before any report
        "3/a1": [{"usage": {"deployment_cost_30d_usd": 1.0}}, {"ceiling": 4.0}],
    }
    for run, bodies in runs.items():
        (tmp_path / run).mkdir(parents=True)
        for k, body in enumerate(bodies):
            name = "eval-ceiling.json" if "ceiling" in body else f"eval-report-{k}.json"
            (tmp_path / run / name).write_text(json.dumps(body))
    # 3/a1 finished one book, then was killed mid-book: at least its ceiling.
    want = {"1/a1": 2.6, "2/a1": 0.0, "2/a2": 4.0, "3/a1": 4.0}
    assert spend.month_spent(tmp_path) == want
    (tmp_path / "3/a1/eval-done.json").write_text("{}")  # it ran to the end
    assert spend.month_spent(tmp_path)["3/a1"] == 1.0
    (tmp_path / "3/a1/eval-report-9.json").write_text('{"usage": {"error": "x"}}')
    assert spend.month_spent(tmp_path)["3/a1"] == 4.0  # its last read failed
    (tmp_path / "3/a1/eval-report-9.json").unlink()
    assert spend.main([str(tmp_path), "--budget", "40", "--cap", "5"]) == 0
    assert capsys.readouterr().out.strip() == "5.00"  # min(32.40 left, cap)
    assert spend.main([str(tmp_path), "--budget", "40", "--cap", "0.001"]) == 1
    assert spend.main([str(tmp_path), "--budget", "10.5"]) == 1  # $2.90 < one book
    assert "::error::" in capsys.readouterr().out


# --- SPEC-94 S2a: the run's unscored tally --------------------------------------


def test_unscored_tallies_sum_over_repeats_and_steps():
    from evals.run import _aggregate, run_unscored

    t = {"rubrics": 1, "unparseable": 1, "claimed_empty": 0, "unavailable": 0}
    p1 = {
        "prose": {"score": 0.5, "unscored": t | {"items": 1}, "book": {"unscored": t}},
        "outline": {"score": None, "unscored": t | {"items": 1}},
    }
    p2 = {
        "prose": {"score": 0.7, "unscored": t | {"items": 1}, "book": {"unscored": t}},
        "outline": {"score": 0.4, "unscored": dict.fromkeys(t, 0) | {"items": 0}},
    }
    steps = _aggregate([p1, p2])
    assert steps["prose"]["score"] == 0.6
    assert steps["prose"]["unscored"]["unparseable"] == 2
    assert steps["prose"]["book"]["unscored"]["unparseable"] == 2
    # The outline's one scored pass is kept, but its tally covers both passes.
    assert steps["outline"]["score"] == 0.4
    assert steps["outline"]["unscored"]["items"] == 1
    assert run_unscored(steps) == {
        "rubrics": 5,
        "unparseable": 5,
        "claimed_empty": 0,
        "unavailable": 0,
        "items": 3,
    }
    assert run_unscored({"x": {"score": 1}})["rubrics"] == 0


def test_the_unscored_tally_is_reported_beside_the_aggregates():
    from evals.harness import report

    run = {
        "book": "cel",
        "app_sha": "s",
        "judge": {"provider": "groq", "self": False},
        "steps": {"outline": {"score": 0.5}},
        "unscored": {
            "rubrics": 2,
            "unparseable": 2,
            "claimed_empty": 0,
            "unavailable": 0,
            "items": 1,
        },
    }
    card = report.scorecard(run)
    assert "unscored: 2 judged rubric(s) not scored (unparseable=2" in card
    out = report.tracer_export(run, model="m")
    assert out["metrics"]["unscored.rubrics"] == 2
    assert out["metrics"]["unscored.items"] == 1
    assert out["labels"]["unscored_rubrics"] == "2"


def test_the_book_tally_covers_every_pass_not_only_the_scored_ones():
    """Review (D19-8): a pass with no numeric score (every scene unscored)
    still carries its book-level RC/RN judgments; the book tally sums every
    pass, while the evidence under the means stays over the scored passes."""
    from evals.run import _aggregate, run_unscored

    zero = {"rubrics": 0, "unparseable": 0, "claimed_empty": 0, "unavailable": 0}
    bad = zero | {"rubrics": 1, "unparseable": 1}
    scored = {
        "score": 0.6,
        "book": {
            "rn_mean": 0.5,
            "rc": 0.7,
            "rn_eligible": 3,
            "rn_scored": 3,
            "unscored": zero,
        },
    }
    unscored = {
        "score": None,
        "book": {"rn_mean": None, "rc": None, "rn_eligible": 3, "unscored": bad},
    }
    # one scored pass: the early return keeps it, but the tally is both passes'
    steps = _aggregate([{"prose": scored}, {"prose": unscored}])
    assert steps["prose"]["score"] == 0.6
    assert steps["prose"]["book"]["rc"] == 0.7
    assert steps["prose"]["book"]["unscored"]["unparseable"] == 1
    assert run_unscored(steps)["unparseable"] == 1
    assert scored["book"]["unscored"] == zero  # the pass itself is not mutated
    # two scored passes and an unscored one: the means and their evidence are
    # the scored passes', the tally all three
    steps = _aggregate([{"prose": scored}, {"prose": unscored}, {"prose": scored}])
    book = steps["prose"]["book"]
    assert book["rc"] == 0.7 and book["rc_scored_passes"] == 2
    assert book["rn_eligible_total"] == 6 and book["rn_scored_total"] == 6
    assert book["unscored"]["unparseable"] == 1
    assert book["unscored"]["rubrics"] == 1
    # no scored pass: the last pass is reported, with every pass's tally
    steps = _aggregate([{"prose": unscored}, {"prose": unscored}])
    assert steps["prose"]["book"]["unscored"]["unparseable"] == 2


@pytest.mark.parametrize("mode", ["claude", "chain", None])
async def test_a_report_records_the_requested_judge_mode(tmp_path, monkeypatch, mode):
    """SPEC-95 E0: the judge block says what was asked for (evals.yml's `judge`
    input) beside the chain it was given and who actually graded."""
    import evals.run as r
    from tests.unit.test_evals_preflight import _cfg

    if mode:
        monkeypatch.setenv("EVAL_JUDGE_MODE", mode)
    else:
        monkeypatch.delenv("EVAL_JUDGE_MODE", raising=False)
    cfg = _cfg(tmp_path)
    judge = SimpleNamespace(provider_id="anthropic", is_self=False, graded_by=[])
    judge.model_override = judge.fell_back = judge.endpoint = None
    monkeypatch.setattr(r, "load_config", lambda: cfg)
    monkeypatch.setattr(r, "Judge", lambda _cfg: judge)
    monkeypatch.setattr(r.get_step("retrieval"), "run", AsyncMock(return_value={}))
    out = await r.run("cel", ["retrieval"], str(tmp_path / "r.json"), 1, None)
    assert out["judge"]["requested_mode"] == mode
    assert out["judge"]["requested"] == cfg.judge_provider
