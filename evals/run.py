"""Eval runner CLI.

    python -m evals.run --book dracula --steps all --out report.json

Drives a RUNNING Polyphony (EVAL_BASE_URL, default http://localhost:8000) as a
throwaway invite-registered user. Steps declare `needs_api`; API steps are
skipped (not failed) when there's no server + admin creds. Adding a step is a
`@step(...)` decorator in evals/steps/pipeline.py — this runner needs no change.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import time
from pathlib import Path

import httpx

from evals.harness import report
from evals.harness.cache import Cache
from evals.harness.client import PolyphonyClient
from evals.harness.config import load as load_config
from evals.harness.judge import Judge
from evals.preflight import RUNS, preflight
from evals.baseline import arms  # noqa: F401 — registers `baseline` (W12-5)
from evals.steps import pipeline  # noqa: F401 — registers the steps
from evals.steps.base import StepContext, all_steps, get_step


def _record_judge_failover(result: dict, judge) -> None:
    """A retired judge model fails over mid-run (Judge._next_in_chain): record
    the judges that actually produced judgments, not the one resolved before
    the steps ran. Several means the run's scores are MIXED (earlier steps by
    one judge, later by another) and the report says so; a chain that gave out
    entirely graded nothing, so the run is marked judge-unavailable instead of
    naming the last provider tried."""
    graded_by = list(getattr(judge, "graded_by", None) or [])
    if getattr(judge, "exhausted", False):
        # Nothing graded at all → judge scores are n/a; graded earlier and
        # then gave out → some steps have judge scores and later ones do not.
        result["judge"]["partial" if graded_by else "unavailable"] = True
    if not graded_by or graded_by == [result["judge"]["provider"]]:
        return
    result["judge"].update(
        provider="+".join(graded_by),
        model=judge.model_override if len(graded_by) == 1 else None,
        self=judge.graded_is_self,
        graded_by=graded_by,
        failed_over=True,
    )
    if len(graded_by) > 1:
        result["judge"]["mixed"] = True


def _git_sha() -> str:
    """HEAD of the checkout the harness runs from ("" outside a git tree)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


async def _app_sha(base_url: str) -> str:
    """The app's /__version sha; else (a local stack reports none) the git
    HEAD the harness runs from, which is the app's when both share a tree."""
    sha = ""
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get(f"{base_url}/__version")
            sha = str(r.json().get("sha") or "")
    except Exception:
        sha = ""
    if sha and sha != "unknown":
        return sha
    return _git_sha() or "unknown"


def _is_quota_error(msg: str) -> bool:
    """True if a step error is a provider daily-quota / rate-limit 429 — the
    signal to stop spending calls for the rest of the run (see run())."""
    m = msg.lower()
    return (
        "ratelimiterror" in m
        or "exceeded your current quota" in m
        or "error code: 429" in m
        or ("429" in m and "quota" in m)
    )


def _aggregate(passes: list[dict]) -> dict:
    """Fold N per-pass step dicts into one: score = mean, plus score_std / repeats.

    A skipped/errored step in any pass is reported from the last pass as-is. Only
    passes that produced a numeric score contribute to the mean/std, so the noise
    band reflects real generations, not failures.
    """
    import statistics

    names = list(passes[-1].keys())
    out = {}
    for name in names:
        scored = [
            p[name]
            for p in passes
            if name in p and isinstance(p[name].get("score"), (int, float))
        ]
        if not scored:
            # No numeric pass: report the last pass verbatim (a lone error or
            # skip surfaces as-is).
            out[name] = passes[-1][name]
            continue
        if len(scored) == 1:
            # One numeric pass (e.g. the judge gave out on the later repeats):
            # keep that score rather than the last, judge-less pass.
            out[name] = scored[0]
            continue
        # Seed from the last SCORED pass (not passes[-1]) so a transient error on
        # the final repeat can't drag an "error" key onto a valid aggregated mean
        # and get the whole step dropped from the Tracer/greenlight export.
        vals = [r["score"] for r in scored]
        merged = dict(scored[-1])
        merged["score"] = round(statistics.mean(vals), 4)
        merged["score_std"] = round(statistics.pstdev(vals), 4)
        merged["score_samples"] = [round(s, 4) for s in vals]
        merged["repeats"] = len(vals)
        out[name] = merged
    return out


async def run(
    book: str, step_names: list[str], out: str, repeat: int = 1, name: str | None = None
) -> dict:
    cfg = load_config()
    text, gt = pipeline.load_corpus(book)
    started = time.time()
    app_sha = await _app_sha(cfg.base_url)
    ctx = StepContext(
        book=book,
        corpus_text=text,
        ground_truth=gt,
        cache=Cache(cfg.cache_dir, app_sha),
        judge=Judge(cfg),
    )
    # When the run began: the prompt contract grades only exchanges the relay
    # archived after this (a reused relay dir holds earlier runs too).
    ctx.run_started = started

    result = {
        "book": book,
        "app_sha": app_sha,
        "harness_sha": _git_sha(),
        # Label of the model under test — what Tracer trends. The app's own
        # LLM_MODEL if set, else the provider's flash default.
        "model": os.getenv("EVAL_MODEL_LABEL")
        or os.getenv("LLM_MODEL")
        or "gemini-3.6-flash",
        # Effective judge (post fail-soft fallback), not merely the requested
        # one — so a run whose judge key was missing is honestly labelled as
        # self-graded in the report/Tracer.
        "judge": {
            "provider": ctx.judge.provider_id,
            "model": ctx.judge.model_override,
            "self": ctx.judge.is_self,
            "requested": cfg.judge_provider,
            "fell_back": ctx.judge.fell_back,
            # A judge at its own URL (a separate relay queue), or None.
            "endpoint": ctx.judge.endpoint,
        },
        "repeat": repeat,
        "run": name,
        "steps": {},
    }

    steps = [get_step(n) for n in step_names]
    have_creds = bool(cfg.admin_email and cfg.admin_password)
    if any(s.needs_api for s in steps) and have_creds:
        ctx.client = PolyphonyClient(cfg.base_url)
        await ctx.client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password)

    try:
        if name:  # a declared run (SPEC-68) is preflighted before any draw
            await preflight(name, step_names, ctx.client, cfg)
        passes = []
        for i in range(max(1, repeat)):
            # Salt the cache per pass so repeats actually re-generate (variance),
            # while a single pass keeps the shared, byte-identical cache keys.
            ctx.cache = Cache(cfg.cache_dir, app_sha, salt=str(i) if repeat > 1 else "")
            one: dict = {}
            quota_exhausted = False
            for s in steps:
                if s.needs_api and ctx.client is None:
                    one[s.name] = {
                        "skipped": True,
                        "reason": "no EVAL_ADMIN_EMAIL/PASSWORD",
                    }
                    continue
                # Once the provider's daily free-tier quota is hit, every later
                # LLM step will 429 too. Skip them (don't record a misleading 0.0
                # that reads as a quality regression) and don't burn retries —
                # the quota won't come back mid-run. Non-API steps still run.
                if quota_exhausted and s.needs_api:
                    one[s.name] = {
                        "skipped": True,
                        "reason": "provider quota exhausted (429) earlier this run",
                    }
                    continue
                try:
                    one[s.name] = await s.run(ctx)
                except Exception as e:  # a failing step never aborts the suite
                    msg = f"{type(e).__name__}: {e}"
                    one[s.name] = {"error": msg}
                    if _is_quota_error(msg):
                        quota_exhausted = True
            passes.append(one)
        result["steps"] = _aggregate(passes)
        if cfg.relay_archive:
            # Over the whole run's archive, once (not per pass).
            try:
                voice = (result["steps"].get("prose") or {}).get("voice") or {}
                revise = result["steps"].get("revise") or {}
                contract = await pipeline.prompt_contract_result(
                    ctx,
                    voice.get("movement"),
                    revise.get("revisions"),
                    voice.get("withheld"),
                )
            except Exception as e:  # a grader bug never loses the run's scores
                contract = {"error": f"{type(e).__name__}: {e}"}
            result["steps"]["prompt_contract"] = contract
        _record_judge_failover(result, ctx.judge)
    finally:
        if ctx.client:
            result["relogin"] = ctx.client.relogins
            await ctx.client.aclose()

    report.write(result, out)
    return result


async def ingest_to_tracer(result: dict, url: str, token: str, env: str) -> None:
    """POST the run to Tracer's /api/ingest (bearer-authed) so the score trend
    shows release-over-release. Best-effort: a failed ingest never fails a run."""
    payload = report.tracer_export(result, model=result["model"], env=env)
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(
            f"{url.rstrip('/')}/api/ingest",
            json=payload,
            headers={"authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        print(f"tracer ingest -> {r.status_code} {r.json()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="dracula")
    ap.add_argument("--steps", default="all", help="comma list or 'all'")
    ap.add_argument("--out", default="eval-report.json")
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each step N times; report score mean +/- std (the noise band). "
        "Re-generates per pass, so N x the LLM cost — use for judge/generation steps.",
    )
    ap.add_argument("--run", choices=list(RUNS), help="a declared run (SPEC-68)")
    ap.add_argument("--export", help="also write the greenlight eval-export JSON here")
    ap.add_argument(
        "--tracer",
        action="store_true",
        help="POST the run to Tracer (EVAL_TRACER_URL + TRACER_INGEST_TOKEN)",
    )
    args = ap.parse_args()

    step_names = (
        all_steps()
        if args.steps == "all"
        else [s.strip() for s in args.steps.split(",")]
    )
    result = asyncio.run(run(args.book, step_names, args.out, args.repeat, args.run))
    print(report.scorecard(result))
    print("\nhistory (reported, not gated)\n" + report.history(report.HISTORY))
    print(f"\nfull report -> {args.out}")
    if args.export:
        import json

        with open(args.export, "w") as f:
            json.dump(report.greenlight_export(result), f, indent=2)
        print(f"greenlight export -> {args.export}")

    if args.tracer:
        url = os.getenv("EVAL_TRACER_URL", "https://tracer.rtrentjones.dev")
        token = os.getenv("TRACER_INGEST_TOKEN", "")
        if not token:
            print("tracer ingest skipped: TRACER_INGEST_TOKEN unset")
        else:
            env_label = os.getenv("EVAL_ENV_LABEL", "prod")
            # Best-effort: a Tracer outage / token mismatch (401) must NOT fail
            # the eval run or discard the scorecard we just computed.
            try:
                asyncio.run(ingest_to_tracer(result, url, token, env_label))
            except Exception as e:  # noqa: BLE001 - telemetry is never fatal
                print(f"tracer ingest failed (non-fatal): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
