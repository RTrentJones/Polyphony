"""W19 S4 (SPEC-94, D19-5): the human ratings against the judges, as aggregates.

python -m evals.calibration.agreement items.json key.json answers.json

`answers.json` is the page's export, {item_id: answer}: for an r2 item a list
of candidate names in line order (or {"1": name, ...}); for a pair "A" or "B"
(1 or 2 also read). Only aggregates are printed, never an item or a verdict.

R2. The truth is each line's credited speaker (`spoken_lines`). The R2 judge
returns a rubric score, not per-line attributions, so "judge accuracy" per line
exists only when the key carries `judge_speakers`; otherwise the human's
per-scene accuracy is put through R2's own bands (≥ 90% → 1.0, 70–89% → 0.7,
50–69% → 0.4, else 0.0) and compared with the judge's score.

Pairs. The panel verdict is the members' agreement (a tie when they differ).
Agreement is over the pairs the panel decided, plus a variant scoring a panel
tie ½; Cohen's κ is human side vs panel side over the decided pairs.

CIs: percentile bootstrap over items (r2: the scene is the cluster), 2,000
resamples, 95%, seeded.
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
    if len(units) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(RESAMPLES):
        v = stat(rng.choices(units, k=len(units)))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    lo = int(len(vals) * (1 - LEVEL) / 2)
    return [round(vals[lo], 4), round(vals[len(vals) - lo - 1], 4)]


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
    out["cohen_kappa_ci"] = bootstrap(decided, _kappa)
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


def agreement(items_doc: dict, key_doc: dict, answers: dict) -> dict:
    items = {it["id"]: it for it in items_doc["items"]}
    key = key_doc["items"]
    if set(items) != set(key):
        raise SystemExit("items.json and key.json are not one export")
    stray = set(answers) - set(items)
    return {
        "r2": r2_stats(items, key, answers),
        "pairs": pair_stats(items, key, answers),
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
