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


def test_chain_uses_first_provider_with_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    assert j.provider_id == "groq"
    assert j.fell_back is False


def test_chain_fails_over_within_chain(monkeypatch):
    # Primary keyless → next chain member grades; NOT flagged as fell_back
    # (any chain member is a deliberate non-self judge choice).
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "openai/gpt-oss-120b")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    assert j.provider_id == "openrouter"
    assert j.fell_back is False
    assert j.is_self is False
    # The explicit model names a groq model — must not follow to openrouter.
    assert j.model_override is None


def test_chain_exhausted_falls_back_to_app_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    assert j.provider_id == "gemini"
    assert j.fell_back is True
    assert j.is_self is True


def test_chain_with_unknown_member_raises(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    with pytest.raises(RuntimeError, match="unknown judge provider"):
        Judge(_cfg(monkeypatch, "groq,nonsense"))


async def test_a_retired_judge_model_falls_through_the_chain(monkeypatch):
    """A 404 (model retired) is not transient: the judge grades with the next
    chain provider instead of failing the step (the weekly run's outline step
    died on a retired Groq model)."""
    import types

    import httpx
    from openai import NotFoundError

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    assert j.provider_id == "groq"
    used = []

    class Fake:
        def __init__(self, pid):
            self.pid = pid

        async def generate(self, *a, **kw):
            used.append(self.pid)
            if self.pid == "groq":
                raise NotFoundError(
                    "model_not_found",
                    response=httpx.Response(
                        404, request=httpx.Request("POST", "http://t")
                    ),
                    body=None,
                )
            return types.SimpleNamespace(
                text='{"score": 0.8, "explanation": "ok"}', model="m"
            )

    monkeypatch.setattr(j, "_ensure", lambda: Fake(j.provider_id))
    judgment = await j.score("rubric", "content")
    assert used == ["groq", "openrouter"]
    assert judgment.score == 0.8 and judgment.judge_provider == "openrouter"


async def test_an_exhausted_chain_is_judge_unavailable_and_steps_keep_metrics(
    monkeypatch,
):
    """Every judge 404s: the score is unavailable, but the step helper hands
    back (None, why) so deterministic metrics and their gates still report."""
    import types

    import httpx
    from openai import NotFoundError

    from evals.harness.judge import JudgeUnavailable
    from evals.harness.report import scorecard, tracer_export
    from evals.steps.pipeline import _judge

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))

    class Gone:
        async def generate(self, *a, **kw):
            raise NotFoundError(
                "model_not_found",
                response=httpx.Response(404, request=httpx.Request("POST", "http://t")),
                body=None,
            )

    monkeypatch.setattr(j, "_ensure", lambda: Gone())
    with pytest.raises(JudgeUnavailable):
        await j.score("rubric", "content")

    j2 = Judge(_cfg(monkeypatch, "groq,openrouter"))
    monkeypatch.setattr(j2, "_ensure", lambda: Gone())
    score, why = await _judge(types.SimpleNamespace(judge=j2), "rubric", "x")
    assert score is None and why.startswith("judge unavailable")

    # A judge-less step renders and exports without crashing on score=None.
    run = {
        "book": "cel",
        "judge": {"provider": "groq", "model": None, "self": False},
        "steps": {
            "outline": {"score": None, "cast_fidelity": 1.0, "judge_explanation": why}
        },
    }
    assert "n/a" in scorecard(run)
    assert tracer_export(run, model="m")["cases"] == []


def test_a_poll_timeout_says_what_the_app_was_doing():
    from evals.harness.client import _state

    plans = {"plans": [{"kind": "outline", "status": "paused", "stage": "chapters"}]}
    assert '"status": "paused"' in _state(plans)
    assert '"stage": "chapters"' in _state(plans)
