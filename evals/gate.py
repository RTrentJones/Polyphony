"""Fail a run when an eval report misses its thresholds (SPEC-06).

    python -m evals.gate eval-report-cel.json "outline.cast_fidelity>=0.9" \
        "extraction.score>0"

Each rule is ``<step>.<field><op><number>`` with op in >=, >, <=, <, ==. A rule
whose step was skipped or errored FAILS (a gate that silently passes on a
missing measurement is decoration — docs/ADR-002 §6).
"""

from __future__ import annotations

import json
import operator
import re
import sys

_OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    ">": operator.gt,
    "<": operator.lt,
}
_RULE = re.compile(r"^(\w+)\.(\w+)\s*(>=|<=|==|>|<)\s*(-?[0-9.]+)$")


def check(report: dict, rules: list[str]) -> list[str]:
    failures = []
    steps = report.get("steps", {})
    for rule in rules:
        m = _RULE.match(rule.strip())
        if not m:
            failures.append(f"bad rule: {rule!r}")
            continue
        step, field, op, num = m.groups()
        res = steps.get(step)
        if not isinstance(res, dict):
            failures.append(f"{rule}: step {step!r} did not run")
            continue
        if res.get("skipped") or res.get("error"):
            reason = res.get("error") or res.get("reason") or "skipped"
            failures.append(f"{rule}: step {step!r} {reason}")
            continue
        val = res.get(field)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            failures.append(f"{rule}: {step}.{field} missing (got {val!r})")
            continue
        if not _OPS[op](val, float(num)):
            failures.append(f"{rule}: got {val}")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        report = json.load(f)
    failures = check(report, argv[1:])
    for rule in argv[1:]:
        print(
            ("FAIL " if any(x.startswith(rule) for x in failures) else "ok   ") + rule
        )
    for f in failures:
        print(f"::error::eval gate: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
