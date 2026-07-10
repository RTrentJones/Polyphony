"""Scorecard rendering + greenlight eval-export emission.

Two outputs from one run:
  * a human scorecard (printed) + full JSON (for diffing releases)
  * the greenlight eval-export schema (checks[] with eval.score/explanation) so
    Tracer's /api/ingest could consume the run later without reshaping.
"""

from __future__ import annotations

import json


def scorecard(run: dict) -> str:
    lines = [
        f"Polyphony evals — book={run['book']} sha={run.get('app_sha','?')[:12]}",
        f"judge={run['judge']['provider']}/{run['judge'].get('model','?')}"
        + (
            "  ⚠ self-grading (judge == model under test)"
            if run["judge"]["self"]
            else ""
        ),
        "-" * 64,
    ]
    for name, res in run["steps"].items():
        if res.get("skipped"):
            lines.append(f"  {name:14s}  SKIPPED  ({res.get('reason','')})")
            continue
        if "error" in res:
            lines.append(f"  {name:14s}  ERROR    {res['error'][:50]}")
            continue
        score = res.get("score")
        extra = ""
        if name == "extraction":
            extra = f"F1={res['f1']} P={res['precision']} R={res['recall']}"
        elif name == "retrieval":
            extra = f"p@k={res['precision_at_k']} mrr={res['mrr']}"
        elif name == "attribution":
            extra = f"acc={res['accuracy']} chance={res['chance']}"
        elif name == "continuity":
            extra = f"recall={res['detection_recall']} fpr={res['false_positive_rate']}"
        lines.append(f"  {name:14s}  {score:.3f}   {extra}")
    return "\n".join(lines)


def greenlight_export(run: dict) -> dict:
    """Shape results as greenlight eval checks[] (score in 0..1)."""
    checks = []
    for name, res in run["steps"].items():
        if res.get("skipped") or "error" in res:
            continue
        checks.append(
            {
                "name": f"eval:{name}",
                "eval": {
                    "score": round(float(res.get("score", 0.0)), 4),
                    "explanation": res.get("judge_explanation", ""),
                },
            }
        )
    return {
        "tool": "polyphony",
        "sha": run.get("app_sha"),
        "book": run["book"],
        "checks": checks,
    }


def write(run: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(run, f, indent=2, ensure_ascii=False)
