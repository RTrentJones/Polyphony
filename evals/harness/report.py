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
        std = res.get("score_std")
        score_str = f"{score:.3f}" + (
            f" ±{std:.3f} (n={res.get('repeats')})" if std is not None else ""
        )
        lines.append(f"  {name:14s}  {score_str}   {extra}")
    return "\n".join(lines)


# A step "passes" when its score clears this floor — a coarse boolean so Tracer
# can render a pass-rate, but the real release-over-release signal is the numeric
# per-case `score`, which is what the dashboard trends. 0.5 is deliberately
# lenient: attribution's chance baseline is ~0.33, so 0.5 already beats chance.
TRACER_PASS_THRESHOLD = 0.5


def tracer_export(
    run: dict,
    *,
    model: str,
    env: str = "eval",
    threshold: float = TRACER_PASS_THRESHOLD,
) -> dict:
    """Shape an eval run as Tracer's EvalRunInput (POST /api/ingest).

    One eval_run + one case per scored step. `model` is the model UNDER TEST
    (what we're trending), not the judge. Skipped/errored steps are dropped so a
    partial run (e.g. keyless, retrieval-only) still ingests cleanly.
    """
    cases = []
    for name, res in run["steps"].items():
        if res.get("skipped") or "error" in res:
            continue
        score = round(float(res.get("score", 0.0)), 4)
        cases.append(
            {
                "name": f"eval:{name}",
                "score": score,
                "passed": score >= threshold,
                "output": _case_output(name, res),
                "judge_rationale": (res.get("judge_explanation") or "")[:16000] or None,
            }
        )
    n = len(cases)
    n_pass = sum(1 for c in cases if c["passed"])
    return {
        "tool": "polyphony",
        "model": model,
        "mode": "eval",
        "env": env,
        "git_sha": run.get("app_sha"),
        "passed": n > 0 and n_pass == n,
        "pass_rate": round(n_pass / n, 4) if n else 0.0,
        "cases": cases,
    }


def _case_output(name: str, res: dict) -> str:
    """A compact, human-readable per-case summary stored on the Tracer case."""
    if name == "extraction":
        return f"F1={res.get('f1')} P={res.get('precision')} R={res.get('recall')} predicted={res.get('predicted')}"
    if name == "retrieval":
        return f"precision@k={res.get('precision_at_k')} mrr={res.get('mrr')}"
    if name == "attribution":
        return f"accuracy={res.get('accuracy')} chance={res.get('chance')} n={res.get('n')}"
    if name == "continuity":
        return f"detection_recall={res.get('detection_recall')} fpr={res.get('false_positive_rate')}"
    if name == "outline":
        return f"nodes={res.get('n_nodes')} structural_ok={res.get('structural_ok')}"
    if name == "prose":
        return f"words={res.get('words')}"
    return ""


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
