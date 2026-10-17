"""Unit tests for evals.run helpers — quota short-circuit classification."""

import pytest

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


def test_preflight_declares_exactly_three_runs():
    """(F) Wave-9 day-0 (SPEC-68): each run's exact --steps, fresh flag and
    minimum daily limit; every step is a registered one."""
    from evals.preflight import RUNS
    from evals.steps.base import all_steps

    full = "extraction,ingestion,retrieval,outline,continuity,prose,revise"
    got = {n: (",".join(r.steps), r.fresh, r.min_daily_tokens) for n, r in RUNS.items()}
    assert got == {
        "gated-full": (full, True, 310_000),
        "gated-scenes": ("outline,prose,revise", True, 260_000),
        "attribution": ("attribution", True, 40_000),
    }
    assert {s for r in RUNS.values() for s in r.steps} <= set(all_steps())
