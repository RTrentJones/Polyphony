"""Scorecard rendering + greenlight eval-export emission.

Two outputs from one run:
  * a human scorecard (printed) + full JSON (for diffing releases)
  * the greenlight eval-export schema (checks[] with eval.score/explanation) so
    Tracer's /api/ingest could consume the run later without reshaping.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from statistics import mean

from evals.graders.prompt_contract import CHECKS as _CONTRACT_CHECKS
from evals.measure.wilson import wilson

HISTORY = Path(__file__).resolve().parent.parent / "history.csv"
ACT_RULE = (
    "Act rule (PM, wave 7): untouched_share < 0.5, R2 mean < 0.90 or tighten RA "
    "mean < 0.85 in both draws of a wave makes the item P0 next wave."
)
POOL_FROM = "W7"  # W8-2 (c): attribution is pooled over W7, W7r and later runs


def scorecard(run: dict) -> str:
    lines = [
        f"Polyphony evals — book={run['book']} sha={run.get('app_sha','?')[:12]}",
        f"judge={run['judge']['provider']}/{run['judge'].get('model') or 'default'}"
        + (f" @ {run['judge']['endpoint']}" if run["judge"].get("endpoint") else "")
        + (
            "  ⚠ self-grading (judge == model under test)"
            if run["judge"]["self"]
            else ""
        )
        + (
            "  ⚠ no judge available: judge scores n/a"
            if run["judge"].get("unavailable")
            else ""
        )
        + (
            "  ⚠ judge gave out mid-run: later steps have no judge score"
            if run["judge"].get("partial")
            else ""
        )
        + (
            "  ⚠ mixed judges (failover mid-run): scores are not comparable"
            if run["judge"].get("mixed")
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
            extra = (
                f"F1={res['f1']} P={res['precision']} R={res['recall']} "
                f"fields={res.get('principal_fields')} "
                f"name_mentions_min={res.get('principal_mentions_min')}"
            )
        elif name == "ingestion":
            extra = f"grounded={res.get('grounded')}/{res.get('n_characters')} avg_chunks={res.get('avg_chunks')}"
        elif name == "retrieval":
            extra = f"p@k={res['precision_at_k']} mrr={res['mrr']}"
        elif name == "attribution":  # W8-2: reported, never gated
            per = (res.get("per_character") or {}).items()
            extra = f"acc={res['accuracy']} chance={res['chance']} " + " ".join(
                f"{c.split()[0]}={v.get('correct')}/{v.get('k')}{v.get('wilson95')}"
                for c, v in per
            )
        elif name == "continuity":
            extra = f"recall={res['detection_recall']} fpr={res['false_positive_rate']}"
        elif name == "outline":
            extra = (
                f"cast={res.get('cast_fidelity')} "
                f"unknown={res.get('unknown_rate')}/{res.get('unknown_rate_canon')} "
                f"secondaries={res.get('outline_names_synopsis_secondaries')}"
            )
        elif name == "prose":
            extra = (
                f"{res.get('path')} scenes={res.get('n_scenes')} "
                f"seams={res.get('beat_seam_max_overlap')}/"
                f"{res.get('scene_seam_max_overlap')} "
                f"r1_capped={res.get('r1_capped_scenes')} "
                f"cast_from_beat={res.get('cast_from_beat')} {res.get('rubric_means')}"
            )
        elif name == "revise":
            extra = (
                f"tighten_n={res.get('tighten_n')} ras={res.get('tighten_ras')} "
                f"outside_identical={res.get('outside_identical')} "
                f"new_names={res.get('new_names')} {res.get('rubric_means')}"
            )
        elif name == "prompt_contract":
            empty = ", ".join(res.get("empty_checks") or [])
            extra = f"empty=[{empty}] " + " ".join(
                f"{k}={res[k]['rate']}"
                for k in _CONTRACT_CHECKS
                if isinstance(res.get(k), dict)
            )
        std = res.get("score_std")
        if score is None:  # the judge was unavailable: metrics only
            lines.append(
                f"  {name:14s}  n/a      {res.get('judge_explanation', '')[:50]}"
            )
            continue
        score_str = f"{score:.3f}" + (
            f" ±{std:.3f} (n={res.get('repeats')})" if std is not None else ""
        )
        lines.append(f"  {name:14s}  {score_str}   {extra}")
    return "\n".join(lines)


def history(path: Path) -> str:
    """W7-6 (a), reported and never gated: one row per past gating draw from
    the checked-in CSV, the act rule under it, and the pairs where it fires."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = list(rows[0])
    lines = ["  ".join(cols), *("  ".join(r[c] or "-" for c in cols) for r in rows)]
    misses: dict[str, dict[str, list[bool]]] = {}
    pooled: dict[str, list[int]] = {}  # name: [correct, k]
    runs = [r["run"] for r in rows]
    pool = runs.index(POOL_FROM) if POOL_FROM in runs else len(runs)
    for i, r in enumerate(rows):
        pair = misses.setdefault(r["pair"], {})
        pair.setdefault("untouched_share", []).append(
            float(r["untouched_share"] or 1) < 0.5
        )
        pair.setdefault("R2 mean", []).append(float(r["r2_mean"]) < 0.90)
        ras = [float(r[c]) for c in cols if c.startswith("tighten_ra_") and r[c]]
        pair.setdefault("tighten RA", []).append(bool(ras) and mean(ras) < 0.85)
        for who, k, n in re.findall(r"(\w+) (\d+)/(\d+)", r["attribution"] or ""):
            if i >= pool:
                tally = pooled.setdefault(who, [0, 0])
                tally[0], tally[1] = tally[0] + int(k), tally[1] + int(n)
    fired = [
        f"{signal} ({p})"
        for p, signals in misses.items()
        for signal, missed in signals.items()
        if len(missed) > 1 and all(missed)
    ]
    attribution = "; ".join(
        f"{who} {k}/{n} {wilson(k, n)}" for who, (k, n) in pooled.items()
    )
    return "\n".join(
        [
            *lines,
            f"Attribution pooled from {POOL_FROM} (Wilson 95%): {attribution or '-'}",
            ACT_RULE,
            f"Fires on: {', '.join(fired) or 'nothing'}",
        ]
    )


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
        if res.get("skipped") or "error" in res or res.get("score") is None:
            continue
        score = round(min(1.0, max(0.0, float(res.get("score", 0.0)))), 4)
        out = _case_output(name, res)
        # Tracer's case schema has no std field, so fold the noise band into the
        # output text — otherwise a stable 0.50±0.00 and a noisy 0.50±0.40 look
        # identical on the trend.
        if res.get("score_std") is not None:
            out = f"score={score} ±{res['score_std']} (n={res.get('repeats')})  {out}"
        cases.append(
            {
                "name": f"eval:{name}",
                "score": score,
                "passed": score >= threshold,
                "output": out,
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
    if name == "ingestion":
        return f"grounded={res.get('grounded')}/{res.get('n_characters')} avg_chunks={res.get('avg_chunks')}"
    if name == "retrieval":
        return f"precision@k={res.get('precision_at_k')} mrr={res.get('mrr')}"
    if name == "attribution":
        return f"accuracy={res.get('accuracy')} chance={res.get('chance')} n={res.get('n')}"
    if name == "continuity":
        return f"detection_recall={res.get('detection_recall')} fpr={res.get('false_positive_rate')}"
    if name == "outline":
        return f"nodes={res.get('n_nodes')} structural_ok={res.get('structural_ok')}"
    if name == "prose":
        return (
            f"words={res.get('words')} path={res.get('path')} "
            f"rubrics={res.get('rubric_means')} "
            f"seams={res.get('beat_seam_max_overlap')}/{res.get('scene_seam_max_overlap')}"
        )
    if name == "prompt_contract":
        return " ".join(
            f"{k}={res[k]['rate']}"
            for k in _CONTRACT_CHECKS
            if isinstance(res.get(k), dict)
        )
    return ""


def greenlight_export(run: dict) -> dict:
    """Shape results as greenlight eval checks[] (score in 0..1)."""
    checks = []
    for name, res in run["steps"].items():
        # A judge-less step (score None) has no 0..1 eval score to export.
        if res.get("skipped") or "error" in res or res.get("score") is None:
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
