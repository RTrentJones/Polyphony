"""Scorecard rendering + greenlight eval-export emission.

Two outputs from one run:
  * a human scorecard (printed) + full JSON (for diffing releases)
  * the greenlight eval-export schema (checks[] with eval.score/explanation) so
    Tracer's /api/ingest could consume the run later without reshaping.
"""

from __future__ import annotations

import csv
import json
import os
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


def _unscored_line(run: dict) -> list[str]:
    """SPEC-94 S2a: the run's unscored tally, beside its aggregates (none
    when the judge scored every rubric it was asked)."""
    t = run.get("unscored") or {}
    if not t.get("rubrics"):
        return []
    return [
        f"unscored: {t['rubrics']} judged rubric(s) not scored "
        f"(unparseable={t.get('unparseable', 0)} "
        f"claimed_empty={t.get('claimed_empty', 0)} "
        f"unavailable={t.get('unavailable', 0)}); {t.get('items', 0)} item(s) "
        "left out of the means"
    ]


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
        f"run={run.get('run') or 'undeclared'}  relogin={run.get('relogin', 0)}",
        *_unscored_line(run),
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
            extra = (
                f"recall={res['detection_recall']} "
                f"fp/1k_control_words={res.get('control_fp_per_1k_words')} "
                f"control_flagged={res.get('control_flagged_share')}"
            ) + (
                f" ⚠ unapplied={[u['id'] for u in res['unapplied_injections']]}"
                if res.get("unapplied_injections")
                else ""
            )
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
            if res.get("book"):  # W18-6/7, beside the score (D18-4)
                b = res["book"]
                extra += f" book: RN={b.get('rn_mean')} RC={b.get('rc')}" + (
                    " (chapter trimmed)" if b.get("rc_trimmed") else ""
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
    move = (run["steps"].get("prompt_contract") or {}).get("movement") or {}
    if "per_principal" in move:  # W9-1 (d): one line, from the gate's tallies
        lines.append(
            "  movement shown/carried/starved: "
            + "; ".join(
                f"{n} {t['shown']}/{t['carried']}/{t['starved']}"
                for n, t in move["per_principal"].items()
            )
            + f"; unmatched withheld {move['unmatched_withheld']}"
        )
    if "thinness" in move:  # wave 11, D5: reported, never gated
        lines.append(
            "  movement store/eligible/shown: "
            + "; ".join(
                f"{n} {t['store']}/{t['eligible']}/{t['shown']}"
                for n, t in move["thinness"].items()
            )
        )
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
        cell = r["attribution"] or ""
        for who, k, n in re.findall(r"(\w+) (\d+)/(\d+)", cell):
            # W16-3: a "neutral:" row (another probe set) is not this pool's.
            if i >= pool and not cell.startswith("neutral:"):
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
    provenance: dict | None = None,
) -> dict:
    """Shape an eval run as Tracer's EvalRunInput (POST /api/ingest).

    One eval_run + one case per scored step. `model` is the model UNDER TEST
    (what we're trending), not the judge. Skipped, errored and unscored steps
    are dropped so a partial run (e.g. keyless, retrieval-only) still ingests
    cleanly, but they are named in `labels.skipped` (unscored ones also in
    `labels.unscored`) / `labels.errored`, with the judge's unavailable/partial
    flags, so an absent step reads as "didn't run", not "fine". A repeated step
    publishes only its averaged score and spread.

    The book is the Tracer `suite`, so training (dracula), held-out
    (frankenstein) and the fidelity gate (cel) trend as separate series. Each
    step's numeric fields ride along as case `metrics`, and `<step>.<metric>`
    run metrics, so Tracer charts F1 / MRR / cast_fidelity over time rather than
    only the coarse pass flag. `provenance` (repo / branch / source_url) comes
    from `github_provenance()` in CI.
    """
    cases, gated = [], []
    run_metrics: dict[str, float] = {}
    skipped, errored, unscored = [], [], []
    for name, res in run["steps"].items():
        if res.get("skipped"):
            skipped.append(name)
            continue
        if "error" in res:
            errored.append(name)
            continue
        if res.get("score") is None:
            # Ran but produced no score: the judge gave out, or generation failed
            # without raising. Listed with the skipped steps so Tracer marks the run
            # incomplete instead of reading it as a complete, all-green run.
            unscored.append(name)
            continue
        score = round(min(1.0, max(0.0, float(res.get("score", 0.0)))), 4)
        out = _case_output(name, res)
        # Tracer's case schema has no std field, so fold the noise band into the
        # output text — otherwise a stable 0.50±0.00 and a noisy 0.50±0.40 look
        # identical on the trend. (It is also a case metric, below.)
        if res.get("score_std") is not None:
            out = f"score={score} ±{res['score_std']} (n={res.get('repeats')})  {out}"
        metrics = {**_numeric_fields(res), **book_metrics(res)}
        if (res.get("repeats") or 1) > 1:
            # _aggregate averages only `score`; every other field is the last draw's.
            # Publish just the averaged score and its spread so a trend never charts
            # one arbitrary draw as if it were the repeated experiment.
            metrics = {
                k: v
                for k, v in metrics.items()
                if k in ("score", "score_std", "repeats", *_BOOK_METRICS)
            }
        for k in _TRACER_RUN_METRICS.get(name, ("score",)):
            if k in metrics:
                run_metrics[f"{name}.{k}"] = metrics[k]
        cases.append(
            {
                "name": f"eval:{name}",
                "category": "reported" if res.get("status") == "reported" else "gated",
                "score": score,
                "passed": score >= threshold,
                "output": out,
                "judge_rationale": (res.get("judge_explanation") or "")[:16000] or None,
                "metrics": metrics,
            }
        )
        if res.get("status") != "reported":  # SPEC-60: trended, never gated
            gated.append(cases[-1])
    for k in ("rubrics", "items"):  # SPEC-94 S2a: trended beside the scores
        v = (run.get("unscored") or {}).get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            run_metrics[f"unscored.{k}"] = v
    n = len(gated)
    n_pass = sum(1 for c in gated if c["passed"])
    judge = run.get("judge") or {}
    labels = {
        k: str(v)[:255]
        for k, v in {
            "judge_provider": judge.get("provider"),
            "judge_model": judge.get("model"),
            "judge_self_graded": judge.get("self"),
            "harness_sha": run.get("harness_sha"),
            "declared_run": run.get("run"),
            "repeat": run.get("repeat"),
            "skipped": ",".join(skipped + unscored) or None,
            "unscored": ",".join(unscored) or None,
            "errored": ",".join(errored) or None,
            # SPEC-94 S2a: judged rubrics the judge did not score this run.
            "unscored_rubrics": (run.get("unscored") or {}).get("rubrics"),
            "judge_unavailable": judge.get("unavailable"),
            "judge_partial": judge.get("partial"),
            # Dispatched on demand, not on a schedule: Tracer must not read a
            # quiet week as "stale".
            "cadence": "on-demand",
            # An experiment is a label, not an env, so every CI run of a book
            # stays on one `env=ci` series and the experiments stay comparable.
            "experiment": os.environ.get("EVAL_EXPERIMENT") or None,
        }.items()
        if v is not None
    }
    out_run = {
        "tool": "polyphony",
        "model": model,
        "mode": "eval",
        "env": env,
        "suite": run.get("book"),
        "git_sha": run.get("app_sha"),
        # No gated case, no pass (a reported-only run, e.g. attribution alone).
        "passed": n > 0 and n_pass == n,
        "pass_rate": round(n_pass / n, 4) if n else 0.0,
        "metrics": dict(list(run_metrics.items())[:64]),
        "labels": labels,
        "cases": cases,
    }
    for k, v in (provenance or {}).items():
        if k in ("repo", "branch", "source_url") and v:
            out_run[k] = v
        elif k == "labels" and v:
            out_run["labels"] = {**v, **out_run["labels"]}
    return out_run


# The headline numbers per step that become run-level `<step>.<metric>` trends
# (every numeric field still rides on the case). Unlisted steps trend `score`.
_TRACER_RUN_METRICS: dict[str, tuple[str, ...]] = {
    "extraction": ("score", "f1", "precision", "recall"),
    "retrieval": ("score", "precision_at_k", "mrr"),
    "attribution": ("score", "accuracy", "chance"),
    "continuity": (
        "score",
        "detection_recall",
        "control_fp_per_1k_words",
        "control_flagged_share",
    ),
    "outline": ("score", "cast_fidelity", "unknown_rate"),
    "prose": ("score", "words", "rn_mean", "rc"),
}
# W18-6/7 (D18-4): the prose step's `book` block, its own metrics (averaged
# over repeats by evals.run._aggregate), never part of its score.
_BOOK_METRICS = ("rn_mean", "rc")


def book_metrics(res: dict) -> dict[str, float]:
    """The numeric `book` fields of a step result ({} without one)."""
    b = res.get("book") or {}
    return {
        k: round(float(b[k]), 6)
        for k in _BOOK_METRICS
        if isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool)
    }


def _numeric_fields(res: dict) -> dict[str, float]:
    """Top-level finite numbers of a step result (bools excluded), Tracer-safe keys."""
    out: dict[str, float] = {}
    for k, v in res.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if v != v or v in (float("inf"), float("-inf")):
            continue
        key = re.sub(r"[^\w.:/-]", "_", str(k), flags=re.ASCII)[:80]
        out[key] = round(float(v), 6) if isinstance(v, float) else v
        if len(out) >= 64:
            break
    return out


def github_provenance(environ: dict | None = None) -> dict:
    """repo / branch / CI run URL / trigger from the GitHub Actions env (empty locally)."""
    e = os.environ if environ is None else environ
    out: dict = {}
    if e.get("GITHUB_REPOSITORY"):
        out["repo"] = e["GITHUB_REPOSITORY"]
        if e.get("GITHUB_RUN_ID"):
            server = e.get("GITHUB_SERVER_URL", "https://github.com")
            out["source_url"] = (
                f"{server}/{e['GITHUB_REPOSITORY']}/actions/runs/{e['GITHUB_RUN_ID']}"
            )
    if e.get("GITHUB_REF_NAME"):
        out["branch"] = e["GITHUB_REF_NAME"]
    if e.get("GITHUB_EVENT_NAME"):
        out["labels"] = {"trigger": e["GITHUB_EVENT_NAME"]}
    return out


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
        return (
            f"detection_recall={res.get('detection_recall')} "
            f"fp_per_1k_control_words={res.get('control_fp_per_1k_words')} "
            f"control_flagged_share={res.get('control_flagged_share')}"
        )
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
    checks, metrics = [], {}
    for name, res in run["steps"].items():
        # A judge-less step (score None) has no 0..1 eval score to export, and a
        # reported one (SPEC-60) is not a check.
        if res.get("skipped") or "error" in res or res.get("score") is None:
            continue
        if res.get("status") == "reported":
            continue
        metrics |= {f"{name}.{k}": v for k, v in book_metrics(res).items()}
        checks.append(
            {
                "name": f"eval:{name}",
                "eval": {
                    "score": round(float(res.get("score", 0.0)), 4),
                    "explanation": res.get("judge_explanation", ""),
                },
            }
        )
    out = {
        "tool": "polyphony",
        "sha": run.get("app_sha"),
        "book": run["book"],
        "checks": checks,
    }
    if metrics:  # D18-4: reported beside the checks, not as one
        out["metrics"] = metrics
    return out


def write(run: dict, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(run, f, indent=2, ensure_ascii=False)
