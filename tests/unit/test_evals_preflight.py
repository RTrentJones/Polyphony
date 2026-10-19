"""W9-3 (b), SPEC-68: a declared eval run is checked before any draw — its
step list, the eval user's daily token limit, a fresh relay queue and cache —
and `plot draft` refuses an archive with no outline:beats record (W8b)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from evals import preflight as pf
from evals import run as run_mod
from evals.graders import plot

pytestmark = pytest.mark.unit

_FULL = "extraction,ingestion,retrieval,outline,continuity,prose,revise".split(",")


def _client(limit):
    usage = AsyncMock(return_value={"daily_token_limit": limit})
    return SimpleNamespace(
        relogins=0, bootstrap_eval_user=AsyncMock(), usage=usage, aclose=AsyncMock()
    )


def _cfg(tmp_path):
    (tmp_path / "answered").mkdir()
    (tmp_path / "cache" / "sha").mkdir(parents=True)  # evals.run made it
    return SimpleNamespace(
        cache_dir=str(tmp_path / "cache"),
        relay_archive=str(tmp_path / "answered"),
        admin_email="a@x",
        admin_password="pw",
        base_url="",
        judge_provider="",
    )


async def test_a_w8r_style_run_passes(tmp_path):
    await pf.preflight("gated-full", _FULL, _client(20_000_000), _cfg(tmp_path))


@pytest.mark.parametrize(
    "case,message",
    [
        ("limit", "daily limit 200000: run needs ≥ 340000"),
        ("steps", "steps outline,prose are not gated-full's"),
        ("answered/1.json", "fresh run, but {t}/answered is not empty"),
        ("cache/sha/1.json", "fresh run, but {t}/cache is not empty"),
        ("relay", "no relay archive: EVAL_RELAY_DIR unset"),
        ("user", "no eval user: EVAL_ADMIN_EMAIL/PASSWORD unset"),
    ],
)
async def test_a_run_that_cannot_finish_fails_with_its_reason(tmp_path, case, message):
    cfg, steps = _cfg(tmp_path), ["outline", "prose"] if case == "steps" else _FULL
    cfg.relay_archive = None if case == "relay" else cfg.relay_archive
    if "/" in case:
        (tmp_path / case).write_text("{}")
    client = {"limit": _client(200_000), "user": None}.get(case, _client(10**7))
    with pytest.raises(pf.PreflightFailed) as e:
        await pf.preflight("gated-full", steps, client, cfg)
    assert str(e.value) == message.format(t=tmp_path)


async def test_evals_run_preflights_before_the_first_step(tmp_path, monkeypatch):
    cfg, out = _cfg(tmp_path), tmp_path / "r.json"
    judge = SimpleNamespace(provider_id="r", model_override=None, is_self=False)
    judge.fell_back = judge.endpoint = None
    monkeypatch.setattr(run_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(run_mod, "Judge", lambda _cfg: judge)
    monkeypatch.setattr(run_mod, "PolyphonyClient", lambda _url: _client(200_000))
    steps = ["outline", "prose", "revise"]
    with pytest.raises(pf.PreflightFailed, match="daily limit 200000"):
        await run_mod.run("cel", steps, str(out), 1, "gated-scenes")
    assert not out.exists()  # no step ran, no report


def test_plot_draft_refuses_an_archive_with_no_outline(tmp_path, capsys):
    # W8b: the draft was pointed at answered-archive/, not .../model/answered.
    answered = tmp_path / "answered-archive" / "model" / "answered"
    answered.mkdir(parents=True)
    rec = {"purpose": "outline:beats", "messages": [], "answer": "[]"}
    (answered / "0001.json").write_text(json.dumps(rec))
    with pytest.raises(SystemExit) as e:
        plot.main(["draft", str(tmp_path / "answered-archive")])
    assert str(e.value) == f"no outline:beats records in {tmp_path}/answered-archive"
    plot.main(["draft", str(answered)])
    assert json.loads(capsys.readouterr().out)["signed_by"] is None
