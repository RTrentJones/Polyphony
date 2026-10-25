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

from app.llm.providers import active_provider
from evals import splits
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
        books = [r["book"] for r in scored if isinstance(r.get("book"), dict)]
        if books:  # W18-6/7: the `book` block's numbers are averaged too
            merged["book"] = dict(books[-1])
            for k in ("rn_mean", "rc"):
                got = [b[k] for b in books if isinstance(b.get(k), (int, float))]
                merged["book"][k] = round(statistics.mean(got), 4) if got else None
            # SPEC-94 S8: the evidence under those means, over the same passes.
            merged["book"]["rc_scored"] = sum(
                isinstance(b.get("rc"), (int, float)) and not isinstance(b["rc"], bool)
                for b in books
            )
            merged["book"]["rc_trimmed"] = sum(bool(b.get("rc_trimmed")) for b in books)
            for k in ("rn_eligible", "rn_scored"):
                got = [b[k] for b in books if isinstance(b.get(k), int)]
                if got:
                    merged["book"][k] = sum(got)
        out[name] = merged
    for name in names:  # SPEC-94 S2a: the unscored tally covers every pass
        tallies = [
            p[name]["unscored"]
            for p in passes
            if isinstance((p.get(name) or {}).get("unscored"), dict)
        ]
        if len(tallies) > 1:
            out[name] = dict(out[name]) | {"unscored": _sum_tallies(tallies)}
        book_tallies = [
            p[name]["book"]["unscored"]
            for p in passes
            if isinstance(((p.get(name) or {}).get("book") or {}).get("unscored"), dict)
        ]
        if len(book_tallies) > 1 and isinstance(out[name].get("book"), dict):
            book = dict(out[name]["book"]) | {"unscored": _sum_tallies(book_tallies)}
            out[name] = dict(out[name]) | {"book": book}
    return out


def _sum_tallies(tallies: list[dict]) -> dict:
    total: dict = {}
    for t in tallies:
        for k, v in t.items():
            if isinstance(v, int) and not isinstance(v, bool):
                total[k] = total.get(k, 0) + v
    return total


def run_unscored(steps: dict) -> dict:
    """SPEC-94 S2a: the run's `unscored` tally, beside its aggregates — every
    step's (and the prose `book` block's) judged-but-unscored rubrics and the
    items they left unscored, summed. Zeroes when the judge scored everything."""
    tallies = []
    for res in steps.values():
        if not isinstance(res, dict):
            continue
        for t in (res.get("unscored"), (res.get("book") or {}).get("unscored")):
            if isinstance(t, dict):
                tallies.append(t)
    zero = {"rubrics": 0, "unparseable": 0, "claimed_empty": 0, "unavailable": 0}
    return zero | {"items": 0} | _sum_tallies(tallies)


def _is_local(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).hostname in ("localhost", "127.0.0.1")


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
        # W15-0 (N1): an external app's spend is not this run's to count.
        "mode": "boot" if _is_local(cfg.base_url) else "external",
        # Label of the model under test — what Tracer trends. The app's own
        # LLM_MODEL if set, else the active provider's registry default.
        "model": os.getenv("EVAL_MODEL_LABEL")
        or os.getenv("LLM_MODEL")
        or active_provider().default_model,
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
        fixed = name and not RUNS[name].fresh and f"eval-{name}@polyphony.eval"
        await ctx.client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password, fixed)

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
        result["unscored"] = run_unscored(result["steps"])
        if cfg.relay_archive and "baseline" not in step_names:  # no contract draw
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
        # The judge runs here, not in the app: its paid calls never reach the
        # app database, so evals/spend.py adds this to the attempt (D14).
        result["judge_cost_usd"] = round(getattr(ctx.judge, "cost_usd", 0.0), 6)
        if ctx.client:
            result["relogin"] = ctx.client.relogins
            try:
                result["usage"] = await ctx.client.usage()  # the report header (W13-1)
            except Exception as e:  # noqa: BLE001 - never hide the run's error
                result["usage"] = {"error": f"{type(e).__name__}: {e}"}
            await ctx.client.aclose()

    report.write(result, out)
    return result


async def ingest_to_tracer(
    result: dict,
    url: str,
    token: str,
    env: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """POST the run to Tracer's /api/ingest (bearer-authed) so the score trend
    shows release-over-release. Best-effort: a failed ingest never fails a run,
    and never raises — but the outcome is always visible (log line, an Actions
    warning on failure, and the job summary), so a silent 401 can't hide a gap.
    Returns whether the run landed."""
    book = result.get("book") or "?"
    if not token:
        _report_ingest(book, "skipped (TRACER_INGEST_TOKEN unset)", ok=False)
        return False
    try:
        payload = report.tracer_export(
            result,
            model=result["model"],
            env=env,
            provenance=report.github_provenance(),
        )
        async with httpx.AsyncClient(timeout=30, transport=transport) as h:
            r = await h.post(
                f"{url.rstrip('/')}/api/ingest",
                json=payload,
                headers={"authorization": f"Bearer {token}"},
            )
    except Exception as e:  # noqa: BLE001 - telemetry is never fatal
        _report_ingest(book, f"failed: {type(e).__name__}: {e}", ok=False)
        return False
    try:
        body = r.json()
    except ValueError:
        body = r.text[:200]
    ok = r.is_success
    _report_ingest(book, f"{r.status_code} {body}", ok=ok)
    return ok


def _report_ingest(book: str, outcome: str, *, ok: bool) -> None:
    """One outcome, three sinks: stdout, an Actions warning when it didn't land,
    and a line in the job summary when running in Actions."""
    outcome = " ".join(str(outcome).split())[:300]  # one line: workflow commands
    print(f"tracer ingest -> {outcome}")
    if not ok:
        print(f"::warning title=Tracer ingest::{book}: {outcome}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        mark = "ok" if ok else "NOT ingested"
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"- Tracer ingest `{book}`: {mark} — `{outcome}`\n")
        except OSError:
            pass


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
    ap.add_argument(
        "--holdout",
        action="store_true",
        help="allow a holdout corpus (SPEC-94 S5): once per release, on frozen code",
    )
    ap.add_argument("--export", help="also write the greenlight eval-export JSON here")
    ap.add_argument(
        "--tracer",
        action="store_true",
        help="POST the run to Tracer (EVAL_TRACER_URL + TRACER_INGEST_TOKEN)",
    )
    args = ap.parse_args()
    if args.holdout:
        if splits.split(args.book) != "holdout":
            raise SystemExit(f"--holdout: {args.book} is not a holdout corpus")
        os.environ[splits.ALLOW_ENV] = args.book
    splits.require(args.book)  # a holdout book without --holdout stops here

    step_names = (
        [s for s in all_steps() if s != "baseline"]  # its own run (SPEC-84)
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
        # Best-effort: a Tracer outage / token mismatch (401) must NOT fail
        # the eval run or discard the scorecard we just computed.
        asyncio.run(
            ingest_to_tracer(
                result,
                os.getenv("EVAL_TRACER_URL", "https://tracer.rtrentjones.dev"),
                os.getenv("TRACER_INGEST_TOKEN", ""),
                os.getenv("EVAL_ENV_LABEL", "prod"),
            )
        )
    if any("error" in s for s in result["steps"].values()):
        raise SystemExit(1)  # a step error is red; a quota skip stays a skip (W13-7)


if __name__ == "__main__":
    main()
