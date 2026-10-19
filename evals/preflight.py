"""Eval preflight (SPEC-68): the declared runs. Day-0 contract, frozen; the
checks against them come with W9-3 (b)."""

import argparse
import asyncio
from pathlib import Path
from typing import NamedTuple

from evals.harness.client import PolyphonyClient
from evals.harness.config import load as load_config


class Run(NamedTuple):
    steps: tuple[str, ...]  # exactly the run's --steps
    fresh: bool  # an empty answered/ and cache dir
    # Measured (characters / 4) x 1.3, rounded up to 10k; sources in SPEC-75.
    min_daily_tokens: int


_FULL = "extraction,ingestion,retrieval,outline,continuity,prose,revise"
RUNS = {
    "gated-full": Run(tuple(_FULL.split(",")), True, 340_000),
    "gated-scenes": Run(("outline", "prose", "revise"), True, 320_000),
    "attribution": Run(("attribution",), True, 40_000),
    # W12-5 dry run: --book cel, ungated (dev, never reported); W11b prose x 1.3.
    "baseline-dev": Run(("baseline",), True, 210_000),
}


class PreflightFailed(SystemExit):
    """A run that cannot finish, stopped before its first draw with the reason."""


async def preflight(name: str, steps: list[str], client, cfg) -> None:
    """`evals.run --run <name>` calls this before the first step (SPEC-68)."""
    run = RUNS[name]
    if tuple(steps) != run.steps:
        raise PreflightFailed(f"steps {','.join(steps)} are not {name}'s")
    if not cfg.relay_archive:
        raise PreflightFailed("no relay archive: EVAL_RELAY_DIR unset")
    for d in [cfg.cache_dir, cfg.relay_archive] if run.fresh else []:
        if any(p.is_file() for p in Path(d).rglob("*")):
            raise PreflightFailed(f"fresh run, but {d} is not empty")
    if client is None:
        raise PreflightFailed("no eval user: EVAL_ADMIN_EMAIL/PASSWORD unset")
    limit = (await client.usage())["daily_token_limit"]
    if limit < run.min_daily_tokens:
        raise PreflightFailed(
            f"daily limit {limit}: run needs ≥ {run.min_daily_tokens}"
        )


async def _main(name: str) -> None:
    cfg = load_config()
    client = PolyphonyClient(cfg.base_url)
    try:
        await client.bootstrap_eval_user(cfg.admin_email, cfg.admin_password)
        await preflight(name, list(RUNS[name].steps), client, cfg)
    finally:
        await client.aclose()
    print(f"preflight {name}: ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Check a declared eval run (SPEC-68).")
    ap.add_argument("--run", required=True, choices=list(RUNS))
    asyncio.run(_main(ap.parse_args().run))
