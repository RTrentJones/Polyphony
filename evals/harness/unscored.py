"""SPEC-94 S2a: which judged rubrics went unscored, and the run's tally.

No app import (no settings, no SECRET_KEY): `evals.harness.rescore` runs on
a saved report with nothing but this, the rubrics and the judge's reason
strings. `evals.steps.pipeline` and `evals.run` use the same functions.
"""

from __future__ import annotations

from evals.harness.judge import is_unparseable

_ZERO = {"rubrics": 0, "unparseable": 0, "claimed_empty": 0, "unavailable": 0}


def scores(judged: dict[str, dict]) -> dict[str, float | None]:
    return {rid: j.get("score") for rid, j in judged.items()}


def unscored_ids(judged: dict[str, dict]) -> set[str]:
    """Rubrics the judge did not score (None and not "not applicable"): an
    unparseable reply, a repeated claim of no input, or no judge at all. Any
    of them makes a composite None (rubrics.scene_score, SPEC-94 S2a)."""
    return {
        rid
        for rid, j in judged.items()
        if j.get("score") is None and not j.get("not_applicable")
    }


def unscored_items(rows: list[dict], rids=None) -> int:
    """How many rows (scenes, revisions) came out None because the judge did
    not score one of their rubrics (of `rids`, when given). A row a
    deterministic 0 decided (R1 capped) is scored, so it is not counted."""
    return sum(
        1
        for r in rows
        if isinstance(r.get("rubrics"), dict)
        and r.get("score") is None
        and unscored_ids(r["rubrics"]) & set(rids or r["rubrics"])
    )


def unscored_tally(judgments, items: int = 0) -> dict[str, int]:
    """SPEC-94 S2a: the run's `unscored` tally beside its aggregates.

    `judgments` are rubric records ({score, explanation, ...}); `items` is
    how many scored items (scenes, revisions, the outline) came out None
    because of them. `rubrics` counts every judged-but-unscored rubric, split
    by why: `unparseable`, `claimed_empty`, `unavailable` (the chain gave out).
    Not-applicable rubrics are not counted: nothing was asked."""
    out = dict(_ZERO)
    for j in judgments:
        if j.get("score") is not None or j.get("not_applicable"):
            continue
        out["rubrics"] += 1
        if j.get("judge_claimed_empty"):
            out["claimed_empty"] += 1
        elif j.get("judge_unparseable") or is_unparseable(j.get("explanation")):
            out["unparseable"] += 1
        elif str(j.get("explanation") or "").startswith("judge unavailable"):
            out["unavailable"] += 1
    out["items"] = items
    return out


def sum_tallies(tallies: list[dict]) -> dict:
    """Integer fields summed (a string field such as `scope` is not)."""
    total: dict = {}
    for t in tallies:
        for k, v in t.items():
            if isinstance(v, int) and not isinstance(v, bool):
                total[k] = total.get(k, 0) + v
    return total


def run_unscored(steps: dict) -> dict:
    """SPEC-94 S2a: the run's `unscored` tally, beside its aggregates — every
    step's (and the prose `book` block's) judged-but-unscored rubrics and the
    items they left unscored, summed. Zeroes when the judge scored everything.

    A step tally marked with a `scope` (rescore's "last pass only" on a
    repeated step) is summed too, and the run tally names those steps in its
    own `scope`, so a partial count is never read as the aggregate's."""
    tallies, partial = [], []
    for name, res in steps.items():
        if not isinstance(res, dict):
            continue
        for t in (res.get("unscored"), (res.get("book") or {}).get("unscored")):
            if isinstance(t, dict):
                tallies.append(t)
                if t.get("scope") and name not in partial:
                    partial.append(name)
    out = dict(_ZERO) | {"items": 0} | sum_tallies(tallies)
    if partial:
        out["scope"] = f"last pass only for repeated steps: {', '.join(partial)}"
    return out
