"""Unit tests for evals.run helpers — quota short-circuit classification."""

import pytest

from evals.run import _aggregate, _is_quota_error

pytestmark = pytest.mark.unit


def test_aggregate_reports_scored_pass_over_trailing_error():
    # One good pass + a later error/quota-skip: report the SCORE, not the error
    # (pass-2 flakiness must not mask pass-1's real result).
    passes = [
        {"prose": {"score": 0.9, "words": 700}},
        {"prose": {"error": "empty scene (status=failed)"}},
    ]
    out = _aggregate(passes)
    assert out["prose"]["score"] == 0.9
    assert "error" not in out["prose"]


def test_aggregate_means_multiple_scored_passes():
    passes = [{"s": {"score": 0.4}}, {"s": {"score": 0.6}}]
    out = _aggregate(passes)
    assert out["s"]["score"] == 0.5
    assert out["s"]["score_std"] == 0.1
    assert out["s"]["repeats"] == 2


def test_aggregate_surfaces_pure_error():
    passes = [{"s": {"error": "boom"}}, {"s": {"error": "boom"}}]
    out = _aggregate(passes)
    assert "error" in out["s"] and "score" not in out["s"]


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
