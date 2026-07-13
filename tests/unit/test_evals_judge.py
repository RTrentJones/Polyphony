"""Unit tests for the eval judge's fail-soft provider resolution."""

import pytest

from evals.harness.config import load as load_config
from evals.harness.judge import Judge

pytestmark = pytest.mark.unit


def _cfg(monkeypatch, judge_provider: str):
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", judge_provider)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    return load_config()


def test_judge_uses_requested_provider_when_key_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    j = Judge(_cfg(monkeypatch, "groq"))
    assert j.provider_id == "groq"
    assert j.fell_back is False
    assert j.is_self is False  # groq judge, gemini under test


def test_judge_falls_back_to_app_provider_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    j = Judge(_cfg(monkeypatch, "groq"))
    assert j.provider_id == "gemini"
    assert j.fell_back is True
    assert j.is_self is True  # honest: this run is self-graded


def test_judge_self_grading_detected_when_requested(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    j = Judge(_cfg(monkeypatch, "gemini"))
    assert j.provider_id == "gemini"
    assert j.fell_back is False
    assert j.is_self is True


def test_unknown_judge_provider_raises(monkeypatch):
    with pytest.raises(RuntimeError, match="unknown judge provider"):
        Judge(_cfg(monkeypatch, "nonsense"))
