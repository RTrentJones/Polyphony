"""Eval runner CLI.

    python -m evals.run --book dracula --steps all --out report.json

Drives a RUNNING Polyphony (EVAL_BASE_URL, default http://localhost:8000) as a
throwaway invite-registered user. Steps that only need embeddings (retrieval,
and the reference side of attribution) run in-process and need no LLM key.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from evals.harness import report
from evals.harness.cache import Cache
from evals.harness.client import PolyphonyClient
from evals.harness.config import load as load_config
from evals.harness.judge import Judge
from evals.steps import pipeline

ALL_STEPS = ["extraction", "retrieval", "attribution", "outline", "continuity", "prose"]
# steps that spend LLM quota / need the API + admin creds
LLM_STEPS = {"extraction", "attribution", "outline", "continuity", "prose"}


async def _app_sha(base_url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get(f"{base_url}/__version")
            return r.json().get("sha", "unknown")
    except Exception:
        return "unknown"


async def run(book: str, steps: list[str], out: str) -> dict:
    cfg = load_config()
    text, gt = pipeline.load_corpus(book)
    app_sha = await _app_sha(cfg.base_url)
    cache = Cache(cfg.cache_dir, app_sha)
    judge = Judge(cfg)

    result = {
        "book": book,
        "app_sha": app_sha,
        "judge": {
            "provider": cfg.judge_provider,
            "model": cfg.judge_model,
            "self": cfg.judge_is_self,
        },
        "steps": {},
    }

    needs_api = any(s in LLM_STEPS for s in steps)
    client = None
    if needs_api:
        if not (cfg.admin_email and cfg.admin_password):
            for s in steps:
                if s in LLM_STEPS:
                    result["steps"][s] = {
                        "skipped": True,
                        "reason": "no EVAL_ADMIN_EMAIL/PASSWORD",
                    }
            steps = [s for s in steps if s not in LLM_STEPS]
        else:
            client = PolyphonyClient(cfg.base_url)
            await client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password)

    try:
        for s in steps:
            try:
                if s == "extraction":
                    result["steps"][s] = await pipeline.step_extraction(
                        client, book, gt, text
                    )
                elif s == "retrieval":
                    result["steps"][s] = await pipeline.step_retrieval(gt)
                elif s == "attribution":
                    result["steps"][s] = await pipeline.step_attribution(
                        client, gt, cache
                    )
                elif s == "outline":
                    result["steps"][s] = await pipeline.step_outline(
                        client, gt, judge, cache
                    )
                elif s == "continuity":
                    result["steps"][s] = await pipeline.step_continuity(
                        client, gt, text, cache
                    )
                elif s == "prose":
                    result["steps"][s] = await pipeline.step_prose(
                        client, gt, judge, cache
                    )
            except Exception as e:  # a failing step never aborts the suite
                result["steps"][s] = {"error": f"{type(e).__name__}: {e}"}
    finally:
        if client:
            await client.aclose()

    report.write(result, out)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="dracula")
    ap.add_argument("--steps", default="all", help="comma list or 'all'")
    ap.add_argument("--out", default="eval-report.json")
    ap.add_argument("--export", help="also write the greenlight eval-export JSON here")
    args = ap.parse_args()

    steps = (
        ALL_STEPS if args.steps == "all" else [s.strip() for s in args.steps.split(",")]
    )
    result = asyncio.run(run(args.book, steps, args.out))
    print(report.scorecard(result))
    print(f"\nfull report -> {args.out}")
    if args.export:
        import json

        with open(args.export, "w") as f:
            json.dump(report.greenlight_export(result), f, indent=2)
        print(f"greenlight export -> {args.export}")


if __name__ == "__main__":
    main()
