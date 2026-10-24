"""Unit tests for the eval judge's fail-soft provider resolution."""

import pytest

from app.llm.prices import cost_usd
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


@pytest.mark.parametrize("status", [404, 413, "daily"])
async def test_a_retired_judge_model_falls_through_the_chain(monkeypatch, status):
    """A 404 (model retired), or a 413 (one request over the vendor's whole
    per-minute limit: run 40030061261's cel outline on Groq), is not transient: the judge grades with the next
    chain provider instead of failing the step (the weekly run's outline step
    died on a retired Groq model). D14: so is a spent DAILY cap (run
    40042289933), through every free judge to a self-graded, priced Claude."""
    import types

    import httpx
    from openai import APIStatusError, NotFoundError

    from app.llm.errors import QuotaExhaustedError

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test")
    monkeypatch.setenv("ANTHROPIC_USERS", "@polyphony.eval")
    j = Judge(_cfg(monkeypatch, "groq,openrouter,anthropic"))
    assert j.provider_id == "groq"
    used = []

    class Fake:
        def __init__(self, pid):
            self.pid = pid

        async def generate(self, *a, **kw):
            used.append((self.pid, kw["model"]))
            if status == "daily" and self.pid != "anthropic":
                raise QuotaExhaustedError(
                    "Error code: 429 - Rate limit reached on "
                    + (
                        "tokens per day (TPD)"
                        if self.pid == "groq"
                        else "free-models-per-day"
                    )
                )
            if self.pid == "groq":
                err = NotFoundError if status == 404 else APIStatusError
                raise err(
                    "model_not_found",
                    response=httpx.Response(
                        status, request=httpx.Request("POST", "http://t")
                    ),
                    body=None,
                )
            return types.SimpleNamespace(
                text='{"score": 0.8, "explanation": "ok"}', model="m", cost_usd=0.02
            )

    monkeypatch.setattr(j, "_ensure", lambda: Fake(j.provider_id))
    await j.score("rubric", "content")
    judgment = await j.score("rubric", "content")
    want = "anthropic" if status == "daily" else "openrouter"
    tried = [p for p, _ in used]
    assert tried == [
        "groq",
        "openrouter",
        *(["anthropic"] if status == "daily" else []),
    ] + [want]
    assert judgment.score == 0.8 and judgment.judge_provider == want
    assert j.graded_by == [want] and not j.exhausted
    assert j.cost_usd == pytest.approx(0.04)  # summed: the report's judge_cost_usd
    assert j.graded_is_self is (status == "daily")  # Claude judging Claude
    if status == "daily":
        assert used[-1] == ("anthropic", "claude-sonnet-5-5")


async def test_an_exhausted_chain_is_judge_unavailable_and_steps_keep_metrics(
    monkeypatch,
):
    """Every judge 404s: the score is unavailable, but the step helper hands
    back (None, why) so deterministic metrics and their gates still report."""
    import types

    import httpx
    from openai import NotFoundError

    from evals.harness.judge import JudgeUnavailable
    from evals.harness.report import greenlight_export, scorecard, tracer_export
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
    assert j.exhausted and j.graded_by == []

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
    pc = {"prompt_contract": {"score": 1.0, "empty_checks": ["a", "b"]}}
    assert "1.000   empty=[a, b]" in scorecard({**run, "steps": pc})
    assert tracer_export(run, model="m")["cases"] == []
    assert greenlight_export(run)["checks"] == []


def test_the_run_records_the_judge_that_actually_graded(monkeypatch):
    import types

    from evals.run import _record_judge_failover

    def judge(**kw):
        base = dict(
            graded_by=[], graded_is_self=False, exhausted=False, model_override=None
        )
        return types.SimpleNamespace(**{**base, **kw})

    result = {"judge": {"provider": "groq", "model": "x", "self": False}}
    _record_judge_failover(result, judge(graded_by=["groq"]))
    assert "failed_over" not in result["judge"]
    # Groq 404ed before grading anything and OpenRouter graded: name OpenRouter.
    _record_judge_failover(result, judge(graded_by=["openrouter"]))
    assert result["judge"]["provider"] == "openrouter"
    assert result["judge"]["failed_over"] and "mixed" not in result["judge"]

    # Groq graded early steps, then 404ed, and OpenRouter graded the rest.
    result = {"judge": {"provider": "groq", "model": None, "self": False}}
    _record_judge_failover(result, judge(graded_by=["groq", "openrouter"]))
    assert result["judge"]["mixed"] is True
    assert result["judge"]["graded_by"] == ["groq", "openrouter"]
    assert result["judge"]["provider"] == "groq+openrouter"

    # Every judge 404ed: nothing graded, so no provider is credited.
    result = {"judge": {"provider": "groq", "model": None, "self": False}}
    _record_judge_failover(result, judge(exhausted=True))
    assert result["judge"]["provider"] == "groq"
    assert result["judge"]["unavailable"] is True
    assert "failed_over" not in result["judge"]

    # Groq graded early steps, then the whole chain gave out: partial, not n/a.
    result = {"judge": {"provider": "groq", "model": None, "self": False}}
    _record_judge_failover(result, judge(graded_by=["groq"], exhausted=True))
    assert result["judge"]["partial"] is True
    assert "unavailable" not in result["judge"]


def test_a_poll_timeout_says_what_the_app_was_doing():
    from evals.harness.client import _state

    plans = {"plans": [{"kind": "outline", "status": "paused", "stage": "chapters"}]}
    assert '"status": "paused"' in _state(plans)
    assert '"stage": "chapters"' in _state(plans)


def test_a_lone_judged_repeat_is_kept_over_a_later_judgeless_one():
    from evals.run import _aggregate

    judged = {"outline": {"score": 0.8, "cast_fidelity": 1.0}}
    judgeless = {"outline": {"score": None, "cast_fidelity": 1.0}}
    assert _aggregate([judged, judgeless])["outline"]["score"] == 0.8
    assert _aggregate([judgeless, judgeless])["outline"]["score"] is None


# --- Judge separation (EVAL_JUDGE_BASE_URL) ------------------------------------


def test_a_judge_at_its_own_url_is_not_self_grading(monkeypatch):
    """The judge relay is a separate endpoint answered by a separate agent:
    the run reports self=false and names the endpoint, whatever the app's
    provider is (here the same stub the model under test uses)."""
    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://127.0.0.1:8298/v1/")
    monkeypatch.setenv("EVAL_JUDGE_LABEL", "claude-judge")
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    j = Judge(load_config())
    assert j.endpoint == "http://127.0.0.1:8298/v1"
    # (F) run-W12: the default model is priced (free), so a ceiling admits it.
    assert cost_usd(j.model_override, 1, 1) is not None
    assert j.provider_id == "claude-judge"
    assert j.is_self is False and j.fell_back is False


def test_a_judge_url_equal_to_the_apps_model_url_is_self(monkeypatch):
    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://127.0.0.1:8299/v1")
    monkeypatch.setenv("EVAL_APP_LLM_BASE_URL", "http://127.0.0.1:8299/v1")
    assert Judge(load_config()).is_self is True


async def test_the_url_judge_calls_that_url_and_reports_it(monkeypatch):
    from unittest.mock import AsyncMock

    from evals.harness.report import scorecard
    from tests.unit.test_llm_client import _fake_response

    monkeypatch.delenv("EVAL_JUDGE_LABEL", raising=False)
    # Unset (empty) key: a relay takes any, so the judge supplies a placeholder.
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "")
    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://127.0.0.1:8298/v1")
    j = Judge(load_config())
    client = j._ensure()
    assert str(client._client.base_url).rstrip("/") == "http://127.0.0.1:8298/v1"
    response = _fake_response()
    response.choices[0].message.content = '{"score": 0.6, "explanation": "ok"}'
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    judgment = await j.score("rubric", "content")
    assert judgment.score == 0.6 and judgment.judge_provider == "relay-judge"
    # The relay files the call under the judge's purpose.
    headers = create.call_args.kwargs["extra_headers"]
    assert headers == {"X-Polyphony-Purpose": "eval_judge"}

    run = {
        "book": "cel",
        "app_sha": "abc",
        "judge": {
            "provider": j.provider_id,
            "model": j.model_override,
            "self": j.is_self,
            "endpoint": j.endpoint,
        },
        "steps": {},
    }
    card = scorecard(run)
    assert "@ http://127.0.0.1:8298/v1" in card and "self-grading" not in card


async def test_app_sha_falls_back_to_git_head(monkeypatch):
    """A local stack's /__version reports no sha; the report still names the
    commit under test (it used to be empty)."""
    from evals import run as run_mod

    monkeypatch.setattr(run_mod, "_git_sha", lambda: "f" * 40)
    assert await run_mod._app_sha("http://127.0.0.1:9") == "f" * 40
