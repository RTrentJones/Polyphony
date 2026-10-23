"""W15-0 (SPEC-90 D1): this month's eval spend, and what a Claude run may use.

Each eval run boots an empty database, so the app's own monthly ceiling sees
only that run. The workflow downloads this month's `eval-report-<run>[-<attempt>]`
artifacts (one per run attempt, each its own database) and asks this module
for the ceiling:

    python -m evals.spend <runs dir> --budget 40 [--cap 5]

An attempt's spend is the MAX of `usage.month_cost_usd` over its boot-mode book
reports (each holds the database's cumulative spend, so summing them
double-counts). An attempt that recorded no cost but left its
`eval-ceiling.json` (killed mid-book) counts at that ceiling; otherwise 0.
Prints the ceiling; refuses when under MIN_LEFT_USD (one book) is left.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MIN_LEFT_USD = 3.0  # one book costs about $2.60 with Opus (SPEC-90 D1)


def run_spend(reports: list[dict], ceiling: float | None = None) -> float:
    """One attempt's spend: the largest month_cost_usd among its boot-mode book
    reports (a report without `mode` counts); with none, its `ceiling`."""
    costs = []
    for r in reports:
        if not isinstance(r, dict) or r.get("mode", "boot") != "boot":
            continue
        usage = r.get("usage")
        cost = usage.get("month_cost_usd") if isinstance(usage, dict) else None
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
    return max(costs) if costs else float(ceiling or 0.0)


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None  # a truncated file says nothing about spend


def month_spent(runs_dir: Path) -> dict[str, float]:
    """{attempt dir: spend}; an attempt is a directory holding eval-report-*.json
    or eval-ceiling.json files directly (one downloaded artifact)."""
    dirs = {f.parent for f in runs_dir.rglob("eval-*.json")}
    out = {}
    for d in sorted(dirs):
        cap = _json(d / "eval-ceiling.json")
        cap = cap.get("ceiling") if isinstance(cap, dict) else None
        reports = [_json(f) for f in sorted(d.glob("eval-report-*.json"))]
        out[str(d.relative_to(runs_dir))] = run_spend(
            reports, cap if isinstance(cap, (int, float)) else None
        )
    return out


def ceiling(budget: float, spent: float, cap: float | None = None) -> float:
    """What this run may spend: the month's remainder, or a tighter run cap."""
    left = budget - spent
    return round(min(left, cap) if cap else left, 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.spend")
    ap.add_argument("runs_dir", type=Path)
    ap.add_argument("--budget", type=float, required=True)
    ap.add_argument("--cap", type=float, default=None)
    a = ap.parse_args(argv)
    per_run = month_spent(a.runs_dir) if a.runs_dir.is_dir() else {}
    spent = round(sum(per_run.values()), 2)
    left = round(a.budget - spent, 2)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(
                f"### Claude eval budget\nspent this month: ${spent:.2f} over "
                f"{len(per_run)} earlier run attempt(s) of ${a.budget:.2f}; "
                f"remaining: ${left:.2f}\n"
            )
    print(
        f"eval spend this month: ${spent:.2f}; remaining ${left:.2f}", file=sys.stderr
    )
    if left <= MIN_LEFT_USD:
        print(
            f"::error::the monthly Claude eval budget (${a.budget:.2f}) has "
            f"${left:.2f} left; refusing to run."
        )
        return 1
    print(f"{ceiling(a.budget, spent, a.cap):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
