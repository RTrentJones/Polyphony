"""W19 S7: does a damaged chapter score below its intact one? Aggregates only.

Reads the `rnrc_validation` results of one or more eval reports (one per book,
or repeats: an item judged more than once is averaged first), rebuilds the key
from the reports' seed (refusing a digest mismatch), and pairs each damaged
item with its chapter's intact item. Per damage type and per location against
RC's trimming (out / partial / in; D19-9), for RC and for RN (RN reads one
scene, the same in every variant: drift is its signal, the rest a control):

- n: chapters with both scores; below / ties / above: damaged < = > intact;
- share_below = below / n; mean_drop = mean(intact - damaged) with a seeded
  bootstrap 95% interval over chapters.

python -m evals.validation.analyze REPORT_OR_DIR... [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from evals.validation.build import build
from evals.validation.step import STEP


def collect(paths: list[Path]) -> tuple[int, str, dict[str, dict[str, list]]]:
    """(seed, digest, {item id: {"rc": [scores], "rn": [scores]}})."""
    files = []
    for p in paths:
        files += sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]
    seeds, scores = set(), defaultdict(lambda: {"rc": [], "rn": []})
    for f in files:
        res = (json.loads(f.read_text("utf-8")).get("steps") or {}).get(STEP) or {}
        v = res.get("validation")
        if not v:
            continue
        seeds.add((v["seed"], v["items_digest"]))
        for i, row in v["judged"].items():
            for rid in ("rc", "rn"):
                if isinstance(row.get(rid), (int, float)):
                    scores[i][rid].append(float(row[rid]))
    if len(seeds) != 1:
        raise SystemExit(f"need results from exactly one item set, got {len(seeds)}")
    ((seed, digest),) = seeds
    return seed, digest, dict(scores)


def pairs(key: dict, scores: dict, rid: str) -> list[dict]:
    """[{damage, variant, location, drop}]: intact minus damaged, per chapter,
    for every damaged item whose chapter's intact item was also scored."""
    mean = {i: sum(s[rid]) / len(s[rid]) for i, s in scores.items() if s.get(rid)}
    intact = {k["chapter"]: i for i, k in key.items() if k["variant"] == "intact"}
    out = []
    for i, k in key.items():
        if k["damage"] is None or i not in mean:
            continue
        base = intact.get(k["chapter"])
        if base not in mean:
            continue
        out.append(
            {
                "damage": k["damage"],
                "variant": k["variant"],
                "location": k["location"],
                "drop": mean[base] - mean[i],
            }
        )
    return out


def bootstrap_ci(xs: list[float], resamples: int, seed: int) -> tuple | None:
    """Percentile 95% interval of the mean, resampling `xs` with replacement."""
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(resamples))
    lo = means[int(0.025 * (resamples - 1))]
    hi = means[int(round(0.975 * (resamples - 1)))]
    return round(lo, 4), round(hi, 4)


def summarize(rows: list[dict], resamples: int = 2000, seed: int = 0) -> dict:
    """One line of aggregates for `rows` (each with a `drop`)."""
    drops = [r["drop"] for r in rows]
    n = len(drops)
    below = sum(1 for d in drops if d > 0)
    ties = sum(1 for d in drops if d == 0)
    return {
        "n": n,
        "below": below,
        "ties": ties,
        "above": n - below - ties,
        "share_below": round(below / n, 4) if n else None,
        "mean_drop": round(sum(drops) / n, 4) if n else None,
        "ci95": bootstrap_ci(drops, resamples, seed),
    }


def table(rows: list[dict], resamples: int = 2000, seed: int = 0) -> dict:
    """{damage: {"all": ..., "visible": ..., "out"/"partial"/"in": ...}}.
    `visible` is out + partial: RC's input shows at least part of the damage."""
    by = defaultdict(list)
    for r in rows:
        by[(r["damage"], "all")].append(r)
        by[(r["damage"], r["location"])].append(r)
        if r["location"] in ("out", "partial"):
            by[(r["damage"], "visible")].append(r)
    out: dict = defaultdict(dict)
    for (damage, where), rs in sorted(by.items()):
        out[damage][where] = summarize(rs, resamples, seed)
    return dict(out)


def analyze(paths: list[Path], resamples: int = 2000) -> dict:
    seed, digest, scores = collect(paths)
    _, key = build(seed)
    if key["items_digest"] != digest:
        raise SystemExit(
            "the reports judged a different item set than this checkout builds"
        )
    return {
        "seed": seed,
        "items_judged": {
            rid: sum(1 for s in scores.values() if s.get(rid)) for rid in ("rc", "rn")
        },
        "RC": table(pairs(key["items"], scores, "rc"), resamples),
        "RN": table(pairs(key["items"], scores, "rn"), resamples),
    }


def render(result: dict) -> str:
    lines = [
        f"RN/RC validation (seed {result['seed']}, judged {result['items_judged']})"
    ]
    for rid in ("RC", "RN"):
        lines.append(f"\n{rid}: damaged below intact, per damage and location")
        lines.append(
            f"  {'damage':24s} {'where':8s}  n  below ties above  share  mean drop [95% CI]"
        )
        for damage, rows in result[rid].items():
            for where, s in rows.items():
                ci = s["ci95"]
                ci_s = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "[n/a]"
                lines.append(
                    f"  {damage:24s} {where:8s} {s['n']:2d}  {s['below']:5d} {s['ties']:4d}"
                    f" {s['above']:5d}  {s['share_below']:.2f}  {s['mean_drop']:+.3f} {ci_s}"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("reports", type=Path, nargs="+")
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    result = analyze(a.reports, a.resamples)
    print(render(result))
    if a.json:
        a.json.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
