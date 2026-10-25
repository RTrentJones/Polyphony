"""SPEC-94 D19-2: recompute a pre-S2 eval report with unparseable judge
replies as None, so pre- and post-S2 draws are compared on one rule.

Before S2 an unparseable judge reply scored 0.0 with the reason
"unparseable judge reply" (evals.harness.judge.UNPARSEABLE). Every judged
rubric records its reason, so those zeros are recognisable in an existing
report and are recomputed here — not re-run:

- each such rubric becomes `score: None, judge_unparseable: True` (a rubric
  capped by a deterministic failure keeps its 0: the failure was measured);
- each scene / revision / outline score is recomputed with S2's composite
  rule (rubrics.scene_score: any judged-but-unscored component -> None);
- the step's `score` (mean over its items), `rubric_means`, the prose `book`
  block's `rn_mean`/`rc`, and the `unscored` tallies are recomputed.

A step that ran with `--repeat N > 1` keeps only its last pass's details, so
its aggregated `score` cannot be recomputed; it is left as recorded and marked
`rescore: "repeated: ..."`. Compare such draws on their per-item rows.

    python -m evals.harness.rescore eval-report.json [out.json]
"""

from __future__ import annotations

import copy
import json
import sys

from evals.graders import rubrics
from evals.harness.judge import is_unparseable


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


def _rescore_items(rows: list[dict], composite) -> int:
    from evals.steps.pipeline import _scores, _unscored_ids

    changed = 0
    for r in rows:
        if any([null_unparseable(j) for j in r["rubrics"].values()]):
            changed += 1
            r["score"] = composite(_scores(r["rubrics"]), _unscored_ids(r["rubrics"]))
    return changed


def rescore_step(name: str, res: dict) -> dict:
    """One step's result, recomputed (a copy; the input is left as it was)."""
    from evals.steps.pipeline import _scores, _unscored_ids, unscored_tally

    res = copy.deepcopy(res)
    if not isinstance(res, dict) or res.get("skipped") or "error" in res:
        return res
    repeated = (res.get("repeats") or 1) > 1
    if name == "outline" and isinstance(res.get("rubrics"), dict):
        judged = res["rubrics"]
        if any([null_unparseable(j) for j in judged.values()]):
            unscored = _unscored_ids(judged)
            score = rubrics.outline_score(_scores(judged), unscored)
            vals = [j.get("score") for j in judged.values()]
            res["judge"] = _mean(vals)
            res["unscored"] = unscored_tally(judged.values(), items=int(bool(unscored)))
            if not repeated:
                ok = res.get("structural_ok", True)
                res["score"] = None if score is None else score if ok else 0.0
    elif name == "prose":
        rows = _rows(res, "scenes")
        if _rescore_items(rows, rubrics.scene_score) and not repeated:
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
            items=sum(
                1
                for r in rows
                if _unscored_ids(r["rubrics"]) & set(rubrics.SCENE_RUBRICS)
            ),
        )
        book = res.get("book")
        if isinstance(book, dict) and not repeated:
            rns = [r["rubrics"].get("RN") for r in rows if r["rubrics"].get("RN")]
            for j in rns:
                null_unparseable(j)
            book["rn_mean"] = _mean([j.get("score") for j in rns])
            explanation = book.get("rc_explanation")
            rc = {"score": book.get("rc"), "explanation": explanation}
            if null_unparseable(rc):
                book["rc"] = None
            book["unscored"] = unscored_tally([rc, *rns])
    elif name == "revise":
        rows = _rows(res, "revisions")
        if _rescore_items(rows, rubrics.revise_score) and not repeated:
            res["score"] = _mean([r.get("score") for r in res.get("revisions") or []])
        res["rubric_means"] = {
            rid: _mean([r["rubrics"].get(rid, {}).get("score") for r in rows])
            for rid in rubrics.REVISE_RUBRICS
        }
        res["unscored"] = unscored_tally(
            [j for r in rows for j in r["rubrics"].values()],
            items=sum(1 for r in rows if _unscored_ids(r["rubrics"])),
        )
    if repeated:
        res["rescore"] = (
            "repeated: per-pass details not kept, step score left as recorded"
        )
    return res


def rescore_report(report: dict) -> dict:
    """A pre-S2 report recomputed under D19-2 (a copy), with the run's
    `unscored` tally."""
    from evals.run import run_unscored

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
