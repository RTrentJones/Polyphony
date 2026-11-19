"""W15-0 (SPEC-90 D1): this month's eval spend, and what a Claude run may use.

Each eval run boots an empty database, so the app's own monthly ceiling sees
only that run. The workflow downloads this month's `eval-report-<run>[-<attempt>]`
artifacts (one per run attempt, each its own database) and asks this module
for the ceiling:

    python -m evals.spend <runs dir> --budget 40 [--cap 5]

An attempt's spend is the MAX of `usage.deployment_cost_30d_usd` over its
boot-mode book reports: each holds the database's cumulative spend, so summing
them double-counts. (`month_cost_usd` is the report's user only, and every book
registers its own user; the app's ceiling is deployment-wide, like this.) An attempt that recorded no cost but left its
`eval-ceiling.json` (killed mid-book) counts at that ceiling; otherwise 0.
Prints the ceiling; refuses when under MIN_LEFT_USD (one book) is left.

SPEC-98: the month is also split into lines by tier (`--tier gate --lines
"gate=30,experiment=35,release=20,calibration=5"`). A run's tier is what its
artifact's `eval-tier.json` says (an attempt without one, every run before
SPEC-98, is an experiment). The run's ceiling is then also bounded by what is
left of its tier's line, and a tier whose line is spent is refused, so one
tier cannot spend another's money. Without `--tier`, the month alone bounds it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MIN_LEFT_USD = 3.0  # one book costs about $2.60 with Opus (SPEC-90 D1)
DEFAULT_TIER = "experiment"  # what an attempt with no eval-tier.json was
# SPEC-98's monthly lines in USD; the $10 left of a $100 month is the reserve.
LINES = {"gate": 30.0, "experiment": 35.0, "release": 20.0, "calibration": 5.0}


def parse_lines(text: str | None) -> dict[str, float]:
    """`"gate=30,experiment=35"` -> {tier: usd}; blank -> LINES. Raises
    ValueError on a malformed entry or an amount that is not positive."""
    if not text or not text.strip():
        return dict(LINES)
    out: dict[str, float] = {}
    for part in text.split(","):
        name, sep, usd = part.partition("=")
        name = name.strip()
        if not sep or not name or name in out:
            raise ValueError(f"bad budget line {part!r}")
        amount = float(usd)
        if not amount > 0:
            raise ValueError(f"budget line {name!r} must be positive")
        out[name] = amount
    return out


def run_spend(
    reports: list[dict], ceiling: float | None = None, done: bool = True
) -> float:
    """One attempt's spend: the largest deployment cost among its boot-mode book
    reports (a report without `mode` counts). An attempt with a `ceiling` counts
    at least that unless it is `done` and every book reported a numeric total
    (a failed usage read leaves its book's spend unknown)."""
    costs, unknown = [], False
    for r in reports:
        if not isinstance(r, dict) or r.get("mode", "boot") != "boot":
            continue
        usage = r.get("usage")
        cost = usage.get("deployment_cost_30d_usd") if isinstance(usage, dict) else None
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
        else:
            unknown = True
    settled = done and not unknown
    floor = 0.0 if settled or ceiling is None else float(ceiling)
    return max([floor, *costs])


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
            reports,
            cap if isinstance(cap, (int, float)) else None,
            (d / "eval-done.json").exists(),
        )
    return out


def month_tiers(runs_dir: Path) -> dict[str, str]:
    """{attempt dir: tier}, over the same attempts as month_spent."""
    out = {}
    for d in sorted({f.parent for f in runs_dir.rglob("eval-*.json")}):
        tier = _json(d / "eval-tier.json")
        tier = tier.get("tier") if isinstance(tier, dict) else None
        out[str(d.relative_to(runs_dir))] = (
            tier if isinstance(tier, str) and tier else DEFAULT_TIER
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
    ap.add_argument("--tier", default=None, help="this run's budget line")
    ap.add_argument("--lines", default=None, help="tier=usd,... (default LINES)")
    a = ap.parse_args(argv)
    lines: dict[str, float] = {}
    if a.tier is not None:
        try:
            lines = parse_lines(a.lines)
        except ValueError as e:
            print(f"::error::{e}")
            return 1
        if a.tier not in lines:
            print(f"::error::tier {a.tier!r} has no budget line ({sorted(lines)}).")
            return 1
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
    run_ceiling = ceiling(a.budget, spent, a.cap)
    if a.tier is not None:
        tiers = month_tiers(a.runs_dir) if a.runs_dir.is_dir() else {}
        line_spent = round(
            sum(v for k, v in per_run.items() if tiers.get(k) == a.tier), 2
        )
        line_left = round(lines[a.tier] - line_spent, 2)
        note = (
            f"tier {a.tier}: spent ${line_spent:.2f} of its "
            f"${lines[a.tier]:.2f} line; remaining ${line_left:.2f}"
        )
        print(note, file=sys.stderr)
        if summary:
            with open(summary, "a") as fh:
                fh.write(note + "\n")
        if line_left <= 0:
            print(f"::error::the {a.tier} budget line is spent; refusing to run.")
            return 1
        run_ceiling = min(run_ceiling, line_left)
    if run_ceiling <= 0:  # the app reads 0 as "no ceiling" (app/llm/spend.py)
        print(f"::error::a run ceiling of ${run_ceiling:.2f} would disable the cap.")
        return 1
    print(f"{run_ceiling:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
