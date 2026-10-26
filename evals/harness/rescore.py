"""SPEC-94 D19-2: recompute a pre-S2 eval report with unparseable judge
replies as None, so pre- and post-S2 draws are compared on one rule.

Before S2 an unparseable judge reply scored 0.0 with the reason
"unparseable judge reply" (evals.harness.judge.UNPARSEABLE). Every judged
rubric records its reason, so those zeros are recognisable in an existing
report and are recomputed here — not re-run:

- each such rubric becomes `score: None, judge_unparseable: True` (a rubric
  capped by a deterministic failure keeps its 0: the failure was measured);
- EVERY scene / revision / outline score is recomputed with the live
  composite rule (rubrics.scene_score / outline_score / revise_score: a
  deterministic 0 stands; otherwise any judged-but-unscored component ->
  None), so a claimed-empty rubric (None already in old records) no longer
  leaves its scene at a partial mean;
- the step's `score` (mean over its items), `rubric_means`, the prose `book`
  block's `rn_mean`/`rc`, and the `unscored` tallies are recomputed.

A step that ran with `--repeat N > 1` keeps only its last pass's details, so
its aggregated `score` cannot be recomputed; it is left as recorded and marked
`rescore: "repeated: ..."`, and its recomputed `unscored` tally carries
`scope: "last pass only"` (the run's tally names such steps in its own
`scope`): it counts one pass, not the aggregate's N. Compare such draws on
their per-item rows.

Imports nothing from the app, so it runs without SECRET_KEY or settings.

    python -m evals.harness.rescore eval-report.json [out.json]
"""

from __future__ import annotations

import copy
import json
import sys

from evals.graders import rubrics
from evals.harness.judge import is_unparseable
from evals.harness.unscored import run_unscored, unscored_items, unscored_tally
from evals.harness.unscored import scores as _scores
from evals.harness.unscored import unscored_ids as _unscored_ids


def null_unparseable(judgment: dict) -> bool:
    """Set a pre-S2 unparseable 0.0 to None in place; True when changed."""
    if not isinstance(judgment, dict) or judgment.get("capped"):
        return False
    if judgment.get("score") == 0.0 and is_unparseable(judgment.get("explanation")):
        judgment.update(score=None, judge_unparseable=True)
        return True
    return False


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 4) if vals else None


def _rows(res: dict, key: str) -> list[dict]:
    return [r for r in res.get(key) or [] if isinstance(r.get("rubrics"), dict)]


def _rescore_items(rows: list[dict], composite) -> None:
    """Every row's composite recomputed with the live rule — not only rows an
    unparseable 0.0 touched: a claimed-empty rubric (already None in a pre-S2
    record) left its row at a partial mean, which S2 makes None (D19-8)."""
    for r in rows:
        for j in r["rubrics"].values():
            null_unparseable(j)
        r["score"] = composite(_scores(r["rubrics"]), _unscored_ids(r["rubrics"]))


_LAST_PASS = "last pass only"


def rescore_step(name: str, res: dict) -> dict:
    """One step's result, recomputed (a copy; the input is left as it was)."""
    res = copy.deepcopy(res)
    if not isinstance(res, dict) or res.get("skipped") or "error" in res:
        return res
    repeated = (res.get("repeats") or 1) > 1
    if name == "outline" and isinstance(res.get("rubrics"), dict):
        judged = res["rubrics"]
        for j in judged.values():
            null_unparseable(j)
        unscored = _unscored_ids(judged)
        ok = res.get("structural_ok", True)
        score = rubrics.outline_score(_scores(judged), unscored, ok)
        res["judge"] = _mean([j.get("score") for j in judged.values()])
        res["unscored"] = unscored_tally(
            judged.values(), items=int(bool(unscored) and score is None)
        )
        if not repeated:
            res["score"] = score
    elif name == "prose":
        rows = _rows(res, "scenes")
        _rescore_items(rows, rubrics.scene_score)
        if not repeated:
            res["score"] = _mean([r.get("score") for r in res.get("scenes") or []])
        res["rubric_means"] = {
            rid: _mean([r["rubrics"].get(rid, {}).get("score") for r in rows])
            for rid in rubrics.SCENE_RUBRICS
        }
        res["unscored"] = unscored_tally(
            [
                j
                for r in rows
                for k, j in r["rubrics"].items()
                if k in rubrics.SCENE_RUBRICS
            ],
            items=unscored_items(rows, rubrics.SCENE_RUBRICS),
        )
        book = res.get("book")
        if isinstance(book, dict) and not repeated:
            rns = [r["rubrics"].get("RN") for r in rows if r["rubrics"].get("RN")]
            book["rn_mean"] = _mean([j.get("score") for j in rns])
            explanation = book.get("rc_explanation")
            rc = {"score": book.get("rc"), "explanation": explanation}
            if null_unparseable(rc):
                book["rc"] = None
            book["unscored"] = unscored_tally([rc, *rns])
    elif name == "revise":
        rows = _rows(res, "revisions")
        _rescore_items(rows, rubrics.revise_score)
        if not repeated:
            res["score"] = _mean([r.get("score") for r in res.get("revisions") or []])
        res["rubric_means"] = {
            rid: _mean([r["rubrics"].get(rid, {}).get("score") for r in rows])
            for rid in rubrics.REVISE_RUBRICS
        }
        res["unscored"] = unscored_tally(
            [j for r in rows for j in r["rubrics"].values()],
            items=unscored_items(rows),
        )
    if repeated:
        res["rescore"] = (
            "repeated: per-pass details not kept, step score left as recorded"
        )
        # SPEC-94 S3: the tally above was counted from the last pass's rows,
        # not the aggregate's N passes; it says so rather than pass for both.
        if isinstance(res.get("unscored"), dict) and name in (
            "outline",
            "prose",
            "revise",
        ):
            res["unscored"] = dict(res["unscored"], scope=_LAST_PASS)
    return res


def rescore_report(report: dict) -> dict:
    """A pre-S2 report recomputed under D19-2 (a copy), with the run's
    `unscored` tally."""
    out = copy.deepcopy(report)
    out["steps"] = {
        name: rescore_step(name, res)
        for name, res in (report.get("steps") or {}).items()
    }
    out["unscored"] = run_unscored(out["steps"])
    out["rescored"] = "SPEC-94 D19-2: unparseable judge replies as None"
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        out = rescore_report(json.load(f))
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    print(f"unscored: {out['unscored']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
