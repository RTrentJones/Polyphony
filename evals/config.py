"""SPEC-98 P1: an evals.yml dispatch, validated once, before anything is spent.

The workflow's mode rules (which inputs need which) grew up as shell guards
inside the steps that use them, so a bad combination failed minutes in, after
the app had booted, and no test covered a rule without running that step's
shell. `validate` holds the rules as data and returns every problem at once:

    python -m evals.config   # reads the INPUT_* environment the workflow sets

Exit 1 prints one `::error::` per problem. The step guards stay where they are
(each still protects its own step); this is the same rule, checked first.
"""

from __future__ import annotations

import os
import re
import sys

from evals import splits
from evals.spend import LINES

ABLATIONS = ("pov_fix", "no_excerpts", "no_voices")
JUDGES = ("chain", "claude", "strict")
DEFAULT_BOOKS = "dracula frankenstein cel"
# Every input the rules read, with the workflow's default.
DEFAULTS = {
    "book": DEFAULT_BOOKS,
    "steps": "all",
    "repeat": "1",
    "base_url": "",
    "holdout": "no",
    "claude": "no",
    "claude_generate": "yes",
    "claude_monthly_usd": "",
    "claude_usd_cap": "",
    "tier": "experiment",
    "app_sha": "",
    "from_runs": "",
    "chapters": "3",
    "replicates": "1",
    "judge": "chain",
    "calibration_run": "",
    "ablation": "",
}
_SHA = re.compile(r"[0-9a-f]{40}")
_USD = re.compile(r"[0-9]+(\.[0-9]+)?")


def _int_in(text: str, low: int, high: int | None) -> bool:
    return text.isdigit() and low <= int(text) and (high is None or int(text) <= high)


def validate(inputs: dict[str, str], ref_type: str = "branch") -> list[str]:
    """Every reason this dispatch must not run; [] when it may. `inputs` holds
    the workflow's inputs by name (a missing one takes its default); `ref_type`
    is GITHUB_REF_TYPE."""
    unknown = sorted(set(inputs) - set(DEFAULTS))
    v = {**DEFAULTS, **{k: (inputs[k] or "").strip() for k in inputs}}
    errors = [f"unknown input {name!r}" for name in unknown]

    def choice(name: str, allowed) -> bool:
        if v[name] in allowed:
            return True
        errors.append(f"{name} must be one of {', '.join(allowed)} (got {v[name]!r})")
        return False

    for name in ("holdout", "claude", "claude_generate"):
        choice(name, ("yes", "no"))
    choice("tier", tuple(LINES))
    claude, external = v["claude"] == "yes", bool(v["base_url"])
    steps = [s for s in v["steps"].split(",") if s]

    if choice("judge", JUDGES) and v["judge"] != "chain":
        if not claude:
            errors.append(f"judge={v['judge']} needs claude=yes")
        if external:
            errors.append(f"judge={v['judge']} needs a booted app (base_url is set)")
        if v["judge"] == "strict" and steps != ["narration_judges"]:
            errors.append("judge=strict is for steps=narration_judges alone")

    if v["ablation"]:
        if not all(flag in ABLATIONS for flag in v["ablation"].split(",")):
            errors.append(
                f"ablation must be a comma list of {', '.join(ABLATIONS)} "
                f"with no spaces (got {v['ablation']!r})"
            )
        if external:
            errors.append("ablation needs a booted app (base_url is set)")

    if v["app_sha"] and not _SHA.fullmatch(v["app_sha"]):
        errors.append("app_sha must be a full 40-character commit sha")
    if v["from_runs"] and not all(r.isdigit() for r in v["from_runs"].split(",")):
        errors.append("from_runs takes comma-separated run ids")
    if "calibration_answers" in steps and not v["calibration_run"].isdigit():
        errors.append("steps=calibration_answers needs calibration_run, a run id")

    for name in ("claude_monthly_usd", "claude_usd_cap"):
        if v[name] and not (_USD.fullmatch(v[name]) and float(v[name]) > 0):
            errors.append(f"{name} must be blank or a positive number of dollars")
    if not _int_in(v["repeat"], 1, None):
        errors.append("repeat must be a whole number, 1 or more")
    if not _int_in(v["chapters"], 0, 12):
        errors.append("chapters must be 0..12")
    if not _int_in(v["replicates"], 1, 6):
        errors.append("replicates must be 1..6")

    books, holdout = v["book"].split(), splits.corpora("holdout")
    if v["holdout"] == "yes":
        if ref_type != "tag":
            errors.append("holdout=yes runs only from a tag")
        if v["book"] != DEFAULT_BOOKS:  # the default means both holdout books
            for book in books:
                if book not in holdout:
                    errors.append(
                        f"holdout=yes grades only holdout corpora, not {book}"
                    )
    else:
        for book in books:
            if book in holdout or book.startswith("holdout"):
                errors.append(f"{book} is a holdout corpus: dispatch with holdout=yes")
    return errors


def main(environ=None) -> int:
    env = os.environ if environ is None else environ
    inputs = {
        name: env[f"INPUT_{name.upper()}"]
        for name in DEFAULTS
        if env.get(f"INPUT_{name.upper()}")
    }
    errors = validate(inputs, env.get("GITHUB_REF_TYPE", "branch"))
    for error in errors:
        print(f"::error title=Eval inputs::{error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
