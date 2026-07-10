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


async def run(book: str, step_names: list[str], out: str) -> dict:
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
        "judge": {
            "provider": cfg.judge_provider,
            "model": cfg.judge_model,
            "self": cfg.judge_is_self,
        },
        "steps": {},
    }

    steps = [get_step(n) for n in step_names]
    have_creds = bool(cfg.admin_email and cfg.admin_password)
    if any(s.needs_api for s in steps) and have_creds:
        ctx.client = PolyphonyClient(cfg.base_url)
        await ctx.client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password)

    try:
        for s in steps:
            if s.needs_api and ctx.client is None:
                result["steps"][s.name] = {
                    "skipped": True,
                    "reason": "no EVAL_ADMIN_EMAIL/PASSWORD",
                }
                continue
            try:
                result["steps"][s.name] = await s.run(ctx)
            except Exception as e:  # a failing step never aborts the suite
                result["steps"][s.name] = {"error": f"{type(e).__name__}: {e}"}
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
    result = asyncio.run(run(args.book, step_names, args.out))
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
            asyncio.run(ingest_to_tracer(result, url, token, env_label))


if __name__ == "__main__":
    main()
