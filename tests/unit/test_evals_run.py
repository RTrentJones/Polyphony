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
