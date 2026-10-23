"""W15-0 (SPEC-90 D1): this month's eval spend, and what a Claude run may use.

Each eval run boots an empty database, so the app's own monthly ceiling sees
only that run. The workflow downloads this month's earlier `eval-report-<run>`
artifacts into one directory per run and asks this module for the ceiling:

    python -m evals.spend <runs dir> --budget 40 [--cap 5]

A run's spend is the MAX of `usage.month_cost_usd` over its book reports (each
holds the run database's cumulative spend, so summing them double-counts); a
report without usage counts 0. Prints the ceiling; refuses under $0.25 left.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MIN_LEFT_USD = 0.25


def run_spend(reports: list[dict]) -> float:
    """One run's spend: the largest month_cost_usd among its book reports."""
    costs = [0.0]
    for r in reports:
        usage = r.get("usage") if isinstance(r, dict) else None
        cost = usage.get("month_cost_usd") if isinstance(usage, dict) else None
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
    return max(costs)


def month_spent(runs_dir: Path) -> dict[str, float]:
    """{run id: spend} for each run directory's eval-report-*.json files."""
    out = {}
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        reports = []
        for f in run.rglob("eval-report-*.json"):
            try:
                reports.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue  # a truncated report says nothing about spend
        out[run.name] = run_spend(reports)
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
                f"{len(per_run)} earlier run(s) of ${a.budget:.2f}; "
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
