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

import httpx

from evals.harness import report
from evals.harness.cache import Cache
from evals.harness.client import PolyphonyClient
from evals.harness.config import load as load_config
from evals.harness.judge import Judge
from evals.steps import pipeline  # noqa: F401 — registers the steps
from evals.steps.base import StepContext, all_steps, get_step


async def _app_sha(base_url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get(f"{base_url}/__version")
            return r.json().get("sha", "unknown")
    except Exception:
        return "unknown"


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
        if len(scored) == 0:
            # No numeric pass — a genuine error/skip. Surface it as-is.
            out[name] = passes[-1][name]
            continue
        if len(scored) == 1:
            # One good pass among N: report THAT pass, not passes[-1] — a
            # trailing error/quota-skip on a later repeat must not mask a real
            # score (e.g. pass-2 generation flakiness hiding pass-1's result).
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


async def run(book: str, step_names: list[str], out: str, repeat: int = 1) -> dict:
    cfg = load_config()
    text, gt = pipeline.load_corpus(book)
    app_sha = await _app_sha(cfg.base_url)
    ctx = StepContext(
        book=book,
        corpus_text=text,
        ground_truth=gt,
        cache=Cache(cfg.cache_dir, app_sha),
        judge=Judge(cfg),
    )

    result = {
        "book": book,
        "app_sha": app_sha,
        # Label of the model under test — what Tracer trends. The app's own
        # LLM_MODEL if set, else the provider's flash default.
        "model": os.getenv("EVAL_MODEL_LABEL")
        or os.getenv("LLM_MODEL")
        or "gemini-2.5-flash",
        # Effective judge (post fail-soft fallback), not merely the requested
        # one — so a run whose judge key was missing is honestly labelled as
        # self-graded in the report/Tracer.
        "judge": {
            "provider": ctx.judge.provider_id,
            "model": ctx.judge.model_override,
            "self": ctx.judge.is_self,
            "requested": cfg.judge_provider,
            "fell_back": ctx.judge.fell_back,
        },
        "repeat": repeat,
        "steps": {},
    }

    steps = [get_step(n) for n in step_names]
    have_creds = bool(cfg.admin_email and cfg.admin_password)
    if any(s.needs_api for s in steps) and have_creds:
        ctx.client = PolyphonyClient(cfg.base_url)
        await ctx.client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password)

    try:
        passes = []
        for i in range(max(1, repeat)):
            # Salt the cache per pass so repeats actually re-generate (variance),
            # while a single pass keeps the shared, byte-identical cache keys.
            pass_salt = str(i) if repeat > 1 else ""
            ctx.cache = Cache(cfg.cache_dir, app_sha, salt=pass_salt)
            # Upload steps mix this into content/title so a re-run gets a fresh
            # content_hash instead of 409-ing on the per-user manuscript dedup.
            ctx.pass_salt = pass_salt
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
    finally:
        if ctx.client:
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
    result = asyncio.run(run(args.book, step_names, args.out, repeat=args.repeat))
    print(report.scorecard(result))
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
