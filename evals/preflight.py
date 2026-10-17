"""Eval preflight (SPEC-68): the declared runs. Day-0 contract, frozen; the
checks against them come with W9-3 (b)."""

from typing import NamedTuple


class Run(NamedTuple):
    steps: tuple[str, ...]  # exactly the run's --steps
    fresh: bool  # an empty answered/ and cache dir
    # Measured (characters / 4) x 1.3, rounded up to 10k; sources in SPEC-68.
    min_daily_tokens: int


_FULL = "extraction,ingestion,retrieval,outline,continuity,prose,revise"
RUNS = {
    "gated-full": Run(tuple(_FULL.split(",")), True, 310_000),
    "gated-scenes": Run(("outline", "prose", "revise"), True, 260_000),
    "attribution": Run(("attribution",), True, 40_000),
}
