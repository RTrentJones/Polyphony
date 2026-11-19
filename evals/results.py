"""SPEC-98 D98-3: a run's reports, committed where they do not expire.

Workflow artifacts last 90 days and are keyed by run id, so an analysis has to
keep its own list of runs. The publish job writes each run attempt as one file
on the orphan `eval-results` branch:

    <tier>/<experiment or "untagged">/<run>[-<attempt>].json

    python -m evals.results <artifact dir> <eval-results checkout> \
        --tier experiment --experiment w22 --run 123 --attempt 1 \
        --ref refs/tags/w22-harness --sha <harness sha>

The file is a bundle: the run's identity, its `eval-report-*.json` reports by
file name, and its ceiling and done markers. Nothing else in the artifact is
copied (the app log and the Tracer export stay in the artifact). A run that
answered calibration items is never published: its reports hold the judges'
answers, and the calibration key's blind depends on those staying out of the
repository. Prints the path written, relative to the checkout, or nothing
when there was nothing to publish.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from evals.spend import LINES

SCHEMA = 1
UNTAGGED = "untagged"
PRIVATE_STEPS = {"calibration_answers"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(text: str | None) -> str:
    """An experiment tag as one path segment; blank -> "untagged"."""
    out = _SAFE.sub("-", (text or "").strip()).strip("-.")
    return out or UNTAGGED


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def bundle(src: Path, identity: dict) -> dict | None:
    """The run's bundle, or None when the artifact holds no readable report or
    any report ran a private step."""
    reports = {}
    for path in sorted(src.glob("eval-report-*.json")):
        report = _load(path)
        if not isinstance(report, dict):
            continue
        if PRIVATE_STEPS & set(report.get("steps") or ()):
            return None
        reports[path.name] = report
    if not reports:
        return None
    return {
        "schema": SCHEMA,
        **identity,
        "ceiling": _load(src / "eval-ceiling.json"),
        "done": (src / "eval-done.json").exists(),
        "reports": reports,
    }


def publish(
    src: Path,
    dest: Path,
    *,
    tier: str,
    experiment: str | None,
    run: str,
    attempt: int = 1,
    ref: str = "",
    sha: str = "",
) -> Path | None:
    """Write the bundle under `dest`; returns its path, or None when nothing
    was published. Raises ValueError for an unknown tier or a run id that is
    not a number."""
    if tier not in LINES:
        raise ValueError(f"unknown tier {tier!r}")
    if not str(run).isdigit() or attempt < 1:
        raise ValueError(f"bad run {run!r} attempt {attempt!r}")
    body = bundle(
        src,
        {
            "run": str(run),
            "attempt": attempt,
            "tier": tier,
            "experiment": (experiment or "").strip(),
            "ref": ref,
            "sha": sha,
        },
    )
    if body is None:
        return None
    name = f"{run}.json" if attempt == 1 else f"{run}-{attempt}.json"
    out = dest / tier / slug(experiment) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return out


def load(dest: Path, tier: str, experiment: str | None) -> list[dict]:
    """Every bundle published for an experiment, in run then attempt order: what
    an analyzer reads in place of a hand-kept run list."""
    found = []
    for path in (dest / tier / slug(experiment)).glob("*.json"):
        body = _load(path)
        if isinstance(body, dict) and body.get("schema") == SCHEMA:
            found.append(body)
    return sorted(found, key=lambda b: (int(b["run"]), b["attempt"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.results")
    ap.add_argument("src", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--tier", required=True)
    ap.add_argument("--experiment", default="")
    ap.add_argument("--run", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--ref", default="")
    ap.add_argument("--sha", default="")
    a = ap.parse_args(argv)
    try:
        out = publish(
            a.src,
            a.dest,
            tier=a.tier,
            experiment=a.experiment,
            run=a.run,
            attempt=a.attempt,
            ref=a.ref,
            sha=a.sha,
        )
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    if out is not None:
        print(out.relative_to(a.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
