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


@pytest.mark.parametrize("busy", [1, 3])
async def test_a_per_minute_limit_waits_on_the_same_judge_then_falls_through(
    monkeypatch, busy
):
    """Run 40272098890: Groq's per-minute limit outlasted the client's retries
    and failed Frankenstein's prose step. A per-minute QuotaExhaustedError now
    waits (twice at most) on the same judge; only a third fails over."""
    import types

    from app.llm.errors import QuotaExhaustedError
    from evals.harness import judge as judge_mod

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    slept, used = [], []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(judge_mod.asyncio, "sleep", no_sleep)

    class Fake:
        def __init__(self, pid):
            self.pid = pid

        async def generate(self, *a, **kw):
            used.append(self.pid)
            if self.pid == "groq" and used.count("groq") <= busy:
                raise QuotaExhaustedError(
                    "Error code: 429 - Rate limit reached on tokens per minute (TPM)"
                )
            return types.SimpleNamespace(
                text='{"score": 0.8, "explanation": "ok"}', model="m", cost_usd=0.0
            )

    monkeypatch.setattr(j, "_ensure", lambda: Fake(j.provider_id))
    judgment = await j.score("rubric", "content")
    want = "groq" if busy == 1 else "openrouter"
    assert judgment.score == 0.8 and judgment.judge_provider == want
    assert slept == [60.0] * min(busy, 2)
    assert used == (["groq", "groq"] if busy == 1 else ["groq"] * 3 + ["openrouter"])


async def test_a_spend_pause_propagates_without_waiting(monkeypatch):
    """Codex P2 on #68: a spend or accounting pause (SpendPausedError, a
    QuotaExhaustedError subclass) is not a per-minute limit: it propagates at
    once, with no wait and no failover."""
    from app.llm.errors import SpendPausedError
    from evals.harness import judge as judge_mod

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    j = Judge(_cfg(monkeypatch, "groq,openrouter"))
    slept, used = [], []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(judge_mod.asyncio, "sleep", no_sleep)

    class Paused:
        async def generate(self, *a, **kw):
            used.append(j.provider_id)
            raise SpendPausedError("monthly ceiling reached")

    monkeypatch.setattr(j, "_ensure", lambda: Paused())
    with pytest.raises(SpendPausedError):
        await j.score("rubric", "content")
    assert slept == [] and used == ["groq"] and not j.exhausted


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


# --- SPEC-94 S2a: an unparseable reply is None, never 0.0 ----------------------


@pytest.mark.parametrize(
    "reply",
    [
        "I would rate this fairly well overall.",
        "",
        '{"explanation": "good"}',  # JSON, but no score
        '{"score": "high", "explanation": "good"}',
        '{"score": null}',
        "[0.7]",
    ],
)
def test_an_unparseable_judge_reply_has_no_score(reply):
    from evals.harness.judge import UNPARSEABLE, _parse, is_unparseable

    score, why = _parse(reply)
    assert score is None  # old: 0.0, indistinguishable from a real zero
    assert why.startswith(UNPARSEABLE) and is_unparseable(why)


@pytest.mark.parametrize(
    "reply, score",
    [
        ('{"score": 0.0, "explanation": "fails"}', 0.0),  # a real zero stays 0
        ('```json\n{"score": 0.7, "explanation": "x"}\n```', 0.7),
        ('{"score": 1.4}', 1.0),
        ("score: 0.4 because", 0.4),
    ],
)
def test_a_parseable_reply_keeps_its_score(reply, score):
    from evals.harness.judge import _parse, is_unparseable

    got, why = _parse(reply)
    assert got == score and not is_unparseable(why)


async def test_judge_score_carries_none_for_an_unparseable_reply(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    j = Judge(_cfg(monkeypatch, "groq"))

    async def gen(prompt, system):
        return SimpleNamespace(text="no idea", model="m")

    monkeypatch.setattr(j, "_generate", gen)
    judgment = await j.score("rubric", "content")
    assert judgment.score is None
    assert judgment.explanation == "unparseable judge reply"


# --- SPEC-94 D19-2: pre-S2 records recomputed with those zeros as None --------


def _old_scene(r3, why="R3 ok"):
    rub = {
        r: {"score": v, "explanation": f"{r} ok"}
        for r, v in {"R1": 1.0, "R2": 0.8, "R4": 0.9, "R5": 0.7}.items()
    }
    rub["R3"] = {"score": r3, "explanation": why}
    from evals.graders import rubrics

    scores = {k: v["score"] for k, v in rub.items()}
    return {"rubrics": rub, "score": rubrics.scene_score(scores)}


def test_rescore_recomputes_a_pre_s2_report_with_unparseable_as_none():
    from evals.harness.rescore import rescore_report

    bad = _old_scene(0.0, "unparseable judge reply")
    good = _old_scene(0.6)
    capped = _old_scene(0.6)
    capped["rubrics"]["R4"] = {
        "score": 0.0,
        "explanation": "unparseable judge reply",
        "capped": "seam",
    }
    report = {
        "steps": {
            "prose": {
                "score": round((bad["score"] + good["score"]) / 2, 4),
                "scenes": [bad, good],
            },
            "outline": {
                "structural_ok": True,
                "score": 0.0,
                "rubrics": {
                    "R1": {"score": 1.0, "explanation": "ok"},
                    "R6": {"score": 0.0, "explanation": "unparseable judge reply"},
                    "R3": {"score": 0.6, "explanation": "ok"},
                },
            },
            "revise": {
                "score": 0.5,
                "repeats": 2,
                "revisions": [
                    {
                        "score": 0.4,
                        "rubrics": {
                            "RA": {
                                "score": 0.0,
                                "explanation": "unparseable judge reply",
                            },
                            "R5": {"score": 0.8, "explanation": "ok"},
                        },
                    }
                ],
            },
            "continuity": {"score": 1.0},
        }
    }
    out = rescore_report(report)
    prose = out["steps"]["prose"]
    assert prose["scenes"][0]["rubrics"]["R3"]["score"] is None
    assert prose["scenes"][0]["score"] is None
    assert prose["score"] == good["score"]
    assert prose["unscored"]["unparseable"] == 1 and prose["unscored"]["items"] == 1
    outline = out["steps"]["outline"]
    assert outline["score"] is None and outline["unscored"]["items"] == 1
    # A repeated step keeps its aggregate (only the last pass's rows exist).
    revise = out["steps"]["revise"]
    assert revise["score"] == 0.5 and revise["rescore"].startswith("repeated")
    assert revise["revisions"][0]["score"] is None
    assert out["unscored"]["unparseable"] == 3 and out["unscored"]["items"] == 3
    # The input is left as it was.
    assert report["steps"]["prose"]["scenes"][0]["rubrics"]["R3"]["score"] == 0.0
    # A capped zero is a measured failure, not an unparseable reply.
    again = rescore_report({"steps": {"prose": {"scenes": [capped]}}})
    assert again["steps"]["prose"]["scenes"][0]["rubrics"]["R4"]["score"] == 0.0
    assert again["unscored"]["rubrics"] == 0


def test_rescore_recomputes_every_row_with_the_live_rule():
    """Review B1: a claimed-empty R2 (None already in the old record, the row
    kept its partial mean) must come out None like the live rule makes it, so
    the step's score and the tally agree."""
    from evals.graders import rubrics
    from evals.harness.rescore import rescore_report
    from evals.steps.pipeline import _scores as scores
    from evals.steps.pipeline import _unscored_ids as unscored_ids

    def row(rub):
        rub = {k: dict(v) for k, v in rub.items()}
        return {"rubrics": rub, "score": rubrics.scene_score(scores(rub))}

    ok = {"score": 0.8, "explanation": "ok"}
    s1 = row(
        {
            "R1": {"score": 1.0, "explanation": "ok"},
            "R2": {
                "score": None,
                "explanation": "No dialogue provided",
                "judge_claimed_empty": True,
            },
            "R3": ok,
            "R4": ok,
            "R5": ok,
        }
    )
    assert s1["score"] == 0.8  # the old partial mean
    s2 = row(
        {
            "R1": {"score": 1.0, "explanation": "ok"},
            "R2": {"score": 0.4, "explanation": "ok"},
            "R3": ok,
            "R4": ok,
            "R5": {"score": 0.0, "explanation": "unparseable judge reply"},
        }
    )
    s3 = row({r: {"score": 0.5, "explanation": "ok"} for r in rubrics.SCENE_RUBRICS})
    scenes = [s1, s2, s3]
    report = {
        "steps": {
            "prose": {
                "score": round(sum(s["score"] for s in scenes) / 3, 4),
                "scenes": scenes,
            }
        }
    }
    out = rescore_report(report)["steps"]["prose"]

    # The live computation over the same (nulled) rubrics.
    live = []
    for s in out["scenes"]:
        live.append(
            rubrics.scene_score(scores(s["rubrics"]), unscored_ids(s["rubrics"]))
        )
    assert live == [None, None, s3["score"]]
    assert [s["score"] for s in out["scenes"]] == live
    assert out["score"] == s3["score"]
    assert out["unscored"] == {
        "rubrics": 2,
        "unparseable": 1,
        "claimed_empty": 1,
        "unavailable": 0,
        "items": 2,
    }


def test_rescore_outline_is_recomputed_unconditionally():
    """B1: an outline whose None was a claimed-empty (no unparseable zero)
    is recomputed too, and a structural failure stays 0.0 (S1)."""
    from evals.harness.rescore import rescore_step

    judged = {
        "R1": {"score": 1.0, "explanation": "ok"},
        "R6": {"score": None, "explanation": "x", "judge_claimed_empty": True},
        "R3": {"score": 0.6, "explanation": "ok"},
    }
    old = {"structural_ok": True, "score": 0.6, "rubrics": judged}
    out = rescore_step("outline", old)
    assert out["score"] is None and out["unscored"]["items"] == 1
    broken = rescore_step("outline", dict(old, structural_ok=False))
    assert broken["score"] == 0.0 and broken["unscored"]["items"] == 0


def test_rescore_marks_a_repeated_steps_tally_as_last_pass_only():
    """Review S3: a --repeat report keeps only the last pass's rows, so the
    recomputed tally must not pass for the aggregate's."""
    from evals.harness.rescore import rescore_report

    rev = {
        "score": 0.5,
        "repeats": 3,
        "revisions": [
            {
                "score": 0.4,
                "rubrics": {
                    "RA": {"score": 0.0, "explanation": "unparseable judge reply"},
                    "R5": {"score": 0.8, "explanation": "ok"},
                },
            }
        ],
    }
    out = rescore_report({"steps": {"revise": rev}})
    assert out["steps"]["revise"]["unscored"]["scope"] == "last pass only"
    assert "revise" in out["unscored"]["scope"]
    single = rescore_report({"steps": {"revise": dict(rev, repeats=1)}})
    assert "scope" not in single["steps"]["revise"]["unscored"]
    assert "scope" not in single["unscored"]


def test_rescore_runs_without_app_settings(tmp_path):
    """Nit: `python -m evals.harness.rescore` needs no SECRET_KEY."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = tmp_path / "r.json"
    src.write_text(json.dumps({"steps": {}}))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}
    root = Path(__file__).resolve().parents[2]
    got = subprocess.run(
        [sys.executable, "-m", "evals.harness.rescore", str(src)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode == 0, got.stderr[-2000:]
    assert json.loads(got.stdout)["unscored"]["rubrics"] == 0


def test_a_claude_first_chain_judges_with_sonnet_then_the_free_judges(monkeypatch):
    """SPEC-95 E0: judge=claude sets the chain anthropic,<free chain>; Claude
    grades first with its fast model (Sonnet, never the Opus default), and a
    run without its key falls to the free judges, which the report names."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test")
    j = Judge(_cfg(monkeypatch, "anthropic,groq,openrouter"))
    assert (j.provider_id, j.model_override) == ("anthropic", "claude-sonnet-5-5")
    assert j.fell_back is False
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    j = Judge(_cfg(monkeypatch, "anthropic,groq,openrouter"))
    assert j.provider_id == "groq" and j.model_override is None
