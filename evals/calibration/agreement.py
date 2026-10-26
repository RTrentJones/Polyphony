"""W19 S4 (SPEC-94, D19-5): the human ratings against the judges, as aggregates.

python -m evals.calibration.agreement items.json key.json answers.json

`answers.json` is the page's export, {item_id: answer}: for an r2 item a list
of candidate names in line order (or {"1": name, ...}); for a pair "A" or "B"
(1 or 2 also read); for a chapter an RC score. Only aggregates are printed, never an item or a verdict.

R2. The truth is each line's credited speaker (`spoken_lines`). The R2 judge
returns a rubric score, not per-line attributions, so "judge accuracy" per line
exists only when the key carries `judge_speakers`; otherwise the human's
per-scene accuracy is put through R2's own bands (≥ 90% → 1.0, 70–89% → 0.7,
50–69% → 0.4, else 0.0) and compared with the judge's score.

Pairs. The panel verdict is the members' agreement (a tie when they differ).
Agreement is over the pairs the panel decided, plus a variant scoring a panel
tie ½; Cohen's κ is human side vs panel side over the decided pairs.

Chapters (S7's `chapter` items, evals.validation.export). The human answers an
RC score on the rubric's anchors (0 / 0.4 / 0.7 / 1.0; a number, or its
string); the key carries the judge's RC (`judge_rc`, its judgments averaged).
On the items with both: the mean absolute difference human - judge, and the
share landing on the same anchor (each snapped to its nearest). Ordering: for
each intact/damaged partner pair the human scored, the share where the human
put damaged below intact, ties apart; the same for the judge on those pairs
where it scored both; and how often human and judge order the pair alike
(below / tie / above). An item's bootstrap cluster is its chapter.

CIs: percentile bootstrap over items (r2: the scene is the cluster), 2,000
resamples, 95%, seeded. κ's CI reports how many resamples it dropped as
degenerate (κ undefined: one category on both sides),
`cohen_kappa_ci_degenerate_resamples`.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean

RESAMPLES, LEVEL, SEED = 2000, 0.95, 0


def r2_band(accuracy: float) -> float:
    """R2's rubric bucket for a share of lines attributed correctly."""
    for floor, score in ((0.9, 1.0), (0.7, 0.7), (0.5, 0.4)):
        if accuracy >= floor - 1e-9:
            return score
    return 0.0


def cohen_kappa(x: list, y: list) -> float | None:
    """Cohen's κ of two raters over the same subjects; None when chance
    agreement is 1 (both raters used one and the same category)."""
    if not x or len(x) != len(y):
        return None
    n = len(x)
    cats = set(x) | set(y)
    po = sum(a == b for a, b in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in cats)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def bootstrap(units: list, stat, seed: int = SEED) -> list[float] | None:
    """Percentile CI of `stat` over resampled `units`; None if too few."""
    return bootstrap_counted(units, stat, seed)[0]


def bootstrap_counted(
    units: list, stat, seed: int = SEED
) -> tuple[list[float] | None, int]:
    """(`bootstrap`'s CI, how many of the RESAMPLES were dropped because
    `stat` was undefined on them — κ when a resample holds one category on
    both sides). The CI is over the rest, so a large count means it rests on
    fewer resamples than it says; it is reported beside it."""
    if len(units) < 2:
        return None, 0
    rng = random.Random(seed)
    vals = []
    for _ in range(RESAMPLES):
        v = stat(rng.choices(units, k=len(units)))
        if v is not None:
            vals.append(v)
    dropped = RESAMPLES - len(vals)
    if not vals:
        return None, dropped
    vals.sort()
    lo = int(len(vals) * (1 - LEVEL) / 2)
    return [round(vals[lo], 4), round(vals[len(vals) - lo - 1], 4)], dropped


def _r(v):
    return None if v is None else round(v, 4)


def _r2_answer(answer, n: int) -> list | None:
    if isinstance(answer, dict):
        answer = [answer.get(str(k)) for k in range(1, n + 1)]
    return answer if isinstance(answer, list) and len(answer) == n else None


def _side(answer) -> str | None:
    return {"A": "A", "B": "B", 1: "A", 2: "B", "1": "A", "2": "B"}.get(answer)


def _pooled(scenes: list[tuple[list, list]]) -> float | None:
    hits = [a == t for got, truth in scenes for a, t in zip(got, truth)]
    return mean(hits) if hits else None


def r2_stats(items: dict, key: dict, answers: dict) -> dict:
    ids = [i for i, it in items.items() if it["kind"] == "r2"]
    rows = []  # (human, truth, judge lines | None, judge score, source)
    for i in ids:
        k = key[i]
        got = _r2_answer(answers.get(i), len(k["speakers"]))
        if got is not None:
            rows.append((got, k["speakers"], k.get("judge_speakers"), k))
    out: dict = {"items": len(ids), "answered": len(rows)}
    if not rows:
        return out
    human = [(g, t) for g, t, _, _ in rows]
    out["lines"] = sum(len(t) for _, t in human)
    out["human_accuracy"] = _r(_pooled(human))
    out["human_accuracy_ci"] = bootstrap(human, _pooled)
    tags = [(g, t) for g, t, _, k in rows if k.get("r2_source") == "tags"]
    out["human_accuracy_tags_only"] = {
        "items": len(tags),
        "accuracy": _r(_pooled(tags)),
    }
    lined = [(g, t, j) for g, t, j, _ in rows if j is not None]
    if lined:
        judge = [(j, t) for _, t, j in lined]
        both = [(g, j) for g, _, j in lined]
        out["judge_accuracy"] = _r(_pooled(judge))
        out["judge_accuracy_ci"] = bootstrap(judge, _pooled)
        out["human_judge_line_agreement"] = _r(_pooled(both))
        out["human_judge_line_agreement_ci"] = bootstrap(both, _pooled)
        out["judge_lines_items"] = len(lined)
    else:
        out["judge_accuracy"] = None
        out["judge_accuracy_note"] = (
            "the R2 judge returns a rubric score, not per-line speakers; "
            "compared by R2's bands below"
        )
    # Per scene: the human's accuracy in R2's bands vs the judge's R2 score.
    per = [(r2_band(_pooled([(g, t)])), mean(k["judge_r2"])) for g, t, _, k in rows]
    out["judge_r2_mean"] = _r(mean(j for _, j in per))
    out["human_r2_band_mean"] = _r(mean(h for h, _ in per))
    exact = lambda ps: mean(abs(h - j) < 0.15 for h, j in ps)  # noqa: E731
    gap = lambda ps: mean(h - j for h, j in ps)  # noqa: E731
    out["band_agreement"] = _r(exact(per))
    out["band_agreement_ci"] = bootstrap(per, exact)
    out["human_minus_judge_r2"] = _r(gap(per))
    out["human_minus_judge_r2_ci"] = bootstrap(per, gap)
    return out


def _agree(ps: list[tuple]) -> float | None:
    """Share of pairs where human == panel; a panel tie scores ½."""
    return mean(0.5 if p == "tie" else float(h == p) for h, p in ps) if ps else None


def _kappa(ps: list[tuple]) -> float | None:
    return cohen_kappa([h for h, _ in ps], [p for _, p in ps])


def pair_stats(items: dict, key: dict, answers: dict) -> dict:
    ids = [i for i, it in items.items() if it["kind"] == "pair"]
    rows = [(_side(answers.get(i)), key[i]) for i in ids]
    rows = [(h, k) for h, k in rows if h]
    out: dict = {"items": len(ids), "answered": len(rows)}
    if not rows:
        return out
    every = [(h, k["panel"]) for h, k in rows if k["panel"] is not None]
    decided = [(h, p) for h, p in every if p != "tie"]
    out["panel_ties"] = sum(p == "tie" for _, p in every)
    out["human_panel_agreement"] = _r(_agree(decided))
    out["human_panel_agreement_ci"] = bootstrap(decided, _agree)
    out["human_panel_agreement_ties_half"] = _r(_agree(every))
    out["cohen_kappa"] = _r(_kappa(decided))
    # κ is undefined on a resample whose pairs all fall on one side for both
    # raters; those resamples are dropped from its CI and counted here.
    out["cohen_kappa_ci"], out["cohen_kappa_ci_degenerate_resamples"] = (
        bootstrap_counted(decided, _kappa)
    )
    out["human_chose_A"] = _r(mean(h == "A" for h, _ in rows))  # side-bias check
    members = sorted({m for _, k in rows for m in k.get("members") or {}})
    out["members"] = {}
    for m in members:
        ps = [(h, k["members"].get(m)) for h, k in rows]
        ps = [(h, p) for h, p in ps if p not in (None, "tie")]
        out["members"][m] = {
            "pairs": len(ps),
            "agreement": _r(_agree(ps)),
            "cohen_kappa": _r(_kappa(ps)),
        }
    return out


ANCHORS = (0.0, 0.4, 0.7, 1.0)
KINDS = ("r2", "pair", "chapter")


def _rc_answer(answer) -> float | None:
    """A human RC score in [0, 1], or None for an unreadable answer."""
    if isinstance(answer, str):
        try:
            answer = float(answer.strip())
        except ValueError:
            return None
    if isinstance(answer, bool) or not isinstance(answer, (int, float)):
        return None
    return float(answer) if 0.0 <= answer <= 1.0 else None


def _judge_rc(k: dict) -> float | None:
    v = k.get("judge_rc")
    if isinstance(v, list):
        v = [x for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return mean(v) if v else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _anchor(x: float) -> float:
    """The nearest of RC's anchors (the lower on an exact midpoint)."""
    return min(ANCHORS, key=lambda a: (abs(a - x), a))


def _order(damaged: float, intact: float) -> str:
    return "below" if damaged < intact else "tie" if damaged == intact else "above"


def _flat(clusters: list[list]) -> list:
    return [x for c in clusters for x in c]


def chapter_stats(items: dict, key: dict, answers: dict) -> dict:
    ids = [i for i, it in items.items() if it["kind"] == "chapter"]
    human = {i: _rc_answer(answers.get(i)) for i in ids}
    human = {i: h for i, h in human.items() if h is not None}
    out: dict = {
        "items": len(ids),
        "answered": len(human),
        "unreadable": sum(1 for i in ids if i in answers) - len(human),
    }
    if not human:
        return out
    judge = {i: _judge_rc(key[i]) for i in ids}
    by_ch: dict = {}
    for i, h in human.items():
        if judge[i] is not None:
            by_ch.setdefault(key[i]["chapter"], []).append((h, judge[i]))
    clusters = [by_ch[c] for c in sorted(by_ch)]
    out["judged"] = sum(len(c) for c in clusters)
    out["chapters"] = len(clusters)
    if clusters:
        mad = lambda cs: mean(abs(h - j) for h, j in _flat(cs))  # noqa: E731
        same = lambda cs: mean(_anchor(h) == _anchor(j) for h, j in _flat(cs))  # noqa
        gap = lambda cs: mean(h - j for h, j in _flat(cs))  # noqa: E731
        out["mean_abs_diff"] = _r(mad(clusters))
        out["mean_abs_diff_ci"] = bootstrap(clusters, mad)
        out["anchor_agreement"] = _r(same(clusters))
        out["anchor_agreement_ci"] = bootstrap(clusters, same)
        out["human_minus_judge"] = _r(gap(clusters))
        out["human_minus_judge_ci"] = bootstrap(clusters, gap)
    # Ordering, over the partner pairs the human scored both halves of.
    rows = []  # (human order, judge order | None)
    for i in ids:
        k = key[i]
        if k.get("variant") == "intact" or k.get("damage") is None:
            continue
        p = k.get("partner")
        if i not in human or p not in human:
            continue
        j = None
        if judge[i] is not None and judge.get(p) is not None:
            j = _order(judge[i], judge[p])
        rows.append((_order(human[i], human[p]), j))
    order: dict = {"pairs": len(rows)}
    if rows:
        hs = [h for h, _ in rows]
        order["human_below"] = _r(mean(h == "below" for h in hs))
        order["human_ties"] = _r(mean(h == "tie" for h in hs))
        both = [(h, j) for h, j in rows if j is not None]
        order["judge_pairs"] = len(both)
        if both:
            alike = lambda ps: mean(h == j for h, j in ps)  # noqa: E731
            order["judge_below"] = _r(mean(j == "below" for _, j in both))
            order["judge_ties"] = _r(mean(j == "tie" for _, j in both))
            order["human_judge_agreement"] = _r(alike(both))
            order["human_judge_agreement_ci"] = bootstrap(both, alike)
    out["ordering"] = order
    return out


def agreement(items_doc: dict, key_doc: dict, answers: dict) -> dict:
    items = {it["id"]: it for it in items_doc["items"]}
    key = key_doc["items"]
    if set(items) != set(key):
        raise SystemExit("items.json and key.json are not one export")
    # An answer counts as known only on an item of a kind measured here (r2,
    # pair, chapter); anything else is reported, not silently dropped.
    stray = set(answers) - {i for i, it in items.items() if it["kind"] in KINDS}
    return {
        "r2": r2_stats(items, key, answers),
        "pairs": pair_stats(items, key, answers),
        "chapters": chapter_stats(items, key, answers),
        "unknown_answer_ids": len(stray),
        "ci": f"{LEVEL:.0%} percentile bootstrap, {RESAMPLES} resamples",
    }


def main(argv: list[str] | None = None) -> None:
    paths = [Path(p) for p in (argv if argv is not None else sys.argv[1:])]
    if len(paths) != 3:
        raise SystemExit(__doc__.split("\n\n")[1])
    items, key, answers = (json.loads(p.read_text("utf-8")) for p in paths)
    print(json.dumps(agreement(items, key, answers), indent=1))


if __name__ == "__main__":
    main()
