"""E4: P(new release's chapter > old release's), per judge. Aggregates only.

Reads `chapters_compare` results from eval reports (files or directories) and
prints, for each judge (sonnet, free) and for their agreement ("panel": the
two judges' outcome when they agree, else a tie, SPEC-84's rule):

- the win rate of `--new` over `--old`, ties ½, a forfeit (a chapter one
  release did not write) a loss for that release, with a seeded bootstrap 95%
  interval resampling chapter pairs (the cluster: a (book, chapter) pair; with
  several judgments of one pair, all of them move together, as SPEC-84's
  slot-cluster bootstrap), 2,000 resamples;
- n pairs, unrated pairs (an order left unanswered), forfeits, and how often
  the two orders agreed (order consistency);
- per book, the same win rate without an interval;
- Fleiss κ between the two judges over the pairs both rated (new/old/tie);
- RC per release and its paired difference (new − old), with the same interval.

python -m evals.chapters.analyze REPORT_OR_DIR... --old SHA --new SHA [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from evals.chapters.compare import STEP

RESAMPLES, LEVEL, SEED = 2000, 0.95, 0
JUDGES = ("sonnet", "free")


def collect(paths: list[Path]) -> list[dict]:
    """Every `chapters_compare` pair, tagged with its book; a pair judged in
    two reports is refused (dispatch again explicitly, not twice by accident)."""
    files: dict[Path, None] = {}
    for p in paths:
        for f in sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]:
            files[f.resolve()] = None
    out, seen = [], set()
    for f in files:
        c = ((json.loads(f.read_text("utf-8")).get("steps") or {}).get(STEP) or {}).get(
            "compare"
        )
        if not c:
            continue
        for pair in c["pairs"]:
            key = (c["book"], pair["position"])
            if key in seen:
                raise SystemExit(f"{key} is judged in more than one report")
            seen.add(key)
            out.append(pair | {"book": c["book"], "releases": c["releases"]})
    return out


def value(outcome: str | None, new: str, old: str) -> float | None:
    if outcome == new:
        return 1.0
    if outcome == old:
        return 0.0
    return 0.5 if outcome == "tie" else None


def judge_outcome(pair: dict, judge: str) -> str | None:
    """The pair's outcome for a judge (or "panel"), a forfeit applied."""
    f = pair.get("forfeit")
    if f and f != "both":  # the release that wrote nothing loses
        (other,) = [s for s in pair["releases"] if s != f]
        return other
    if f == "both":
        return None
    js = pair.get("judges") or {}
    if judge != "panel":
        return (js.get(judge) or {}).get("outcome")
    got = [(js.get(j) or {}).get("outcome") for j in JUDGES]
    if None in got:
        return None
    return got[0] if got[0] == got[1] else "tie"


def bootstrap(clusters: dict, seed: int = SEED, level: float = LEVEL) -> dict:
    """{cluster: [values]} -> pooled mean with a cluster bootstrap interval."""
    keys = sorted(clusters)
    if not keys:
        return {"p": None, "ci": None, "n": 0}
    rate = lambda ks: mean(v for k in ks for v in clusters[k])  # noqa: E731
    rng = random.Random(seed)
    boot = sorted(rate(rng.choices(keys, k=len(keys))) for _ in range(RESAMPLES))
    lo = int(RESAMPLES * (1 - level) / 2)
    return {
        "p": round(rate(keys), 4),
        "ci": [round(boot[lo], 4), round(boot[-lo - 1], 4)],
        "n": len(keys),
    }


def win_rate(pairs: list[dict], judge: str, new: str, old: str) -> dict:
    clusters, unrated, consistent, ordered = {}, 0, 0, 0
    per_book: dict = defaultdict(list)
    for p in pairs:
        v = value(judge_outcome(p, judge), new, old)
        if v is None:
            unrated += p.get("forfeit") != "both"
            continue
        clusters[(p["book"], p["position"])] = [v]
        per_book[p["book"]].append(v)
        if judge in JUDGES and not p.get("forfeit"):
            ordered += 1
            consistent += p["judges"][judge]["outcome"] != "tie"
    out = bootstrap(clusters) | {
        "unrated": unrated,
        "forfeits": sum(1 for p in pairs if p.get("forfeit") not in (None, "both")),
        "both_empty": sum(1 for p in pairs if p.get("forfeit") == "both"),
        "per_book": {b: round(mean(v), 4) for b, v in sorted(per_book.items())},
    }
    if judge in JUDGES:
        out["order_consistent"] = f"{consistent}/{ordered}"
    return out


def kappa(pairs: list[dict], new: str, old: str) -> float | None:
    """Fleiss κ of the two judges over the pairs both rated (SPEC-84's function)."""
    from evals.baseline.panel import fleiss

    table = []
    for p in pairs:
        got = [judge_outcome(p, j) for j in JUDGES]
        if p.get("forfeit") or None in got:
            continue
        table.append([sum(g == c for g in got) for c in (new, old, "tie")])
    return round(fleiss(table), 4) if table else None


def rc(pairs: list[dict], judge: str, new: str, old: str) -> dict:
    scores, diffs, trimmed = {new: [], old: []}, {}, 0
    for p in pairs:
        got = (p.get("rc") or {}).get(judge) or {}
        s = {sha: (got.get(sha) or {}).get("score") for sha in (new, old)}
        trimmed += sum(bool((got.get(sha) or {}).get("trimmed")) for sha in (new, old))
        for sha in (new, old):
            if isinstance(s[sha], (int, float)):
                scores[sha].append(s[sha])
        if all(isinstance(v, (int, float)) for v in s.values()):
            diffs[(p["book"], p["position"])] = [s[new] - s[old]]
    return {
        "new_mean": round(mean(scores[new]), 4) if scores[new] else None,
        "old_mean": round(mean(scores[old]), 4) if scores[old] else None,
        "new_minus_old": bootstrap(diffs),
        "trimmed_chapters": trimmed,
    }


def power(
    n: int, p: float, ties: float, sims: int = 400, seed: int = 0, resamples: int = 500
) -> float:
    """Share of simulated runs of n pairs (true win rate p, a `ties` share of
    order-flipped pairs) whose bootstrap 95% interval excludes 0.5 upward."""
    a = p - ties / 2
    if not 0 <= a <= 1 - ties:
        raise ValueError("p and ties are inconsistent")
    rng = random.Random(seed)
    hits = 0
    for _ in range(sims):
        xs = rng.choices([1.0, 0.5, 0.0], weights=[a, ties, 1 - ties - a], k=n)
        boot = sorted(mean(rng.choices(xs, k=n)) for _ in range(resamples))
        hits += boot[int(resamples * (1 - LEVEL) / 2)] > 0.5
    return hits / sims


def resolve(prefix: str, pairs: list[dict]) -> str:
    """The one release sha in the reports that starts with `prefix`."""
    got = {s for p in pairs for s in p["releases"] if s.startswith(prefix)}
    if len(got) != 1:
        raise SystemExit(f"{prefix!r} names {len(got)} releases in the reports")
    return got.pop()


def analyze(pairs: list[dict], new: str, old: str) -> dict:
    if not pairs:
        raise SystemExit(f"no {STEP} pairs in the reports")
    new, old = resolve(new, pairs), resolve(old, pairs)
    for p in pairs:
        if sorted(p["releases"]) != sorted([new, old]):
            raise SystemExit(
                f"{p['book']} compares {p['releases']}, not {new} vs {old}"
            )
    out = {
        "label": "E4, SPEC-95; exploratory unless the signed pre-registration says otherwise",
        "new": new,
        "old": old,
        "pairs": len(pairs),
        "books": sorted({p["book"] for p in pairs}),
    }
    for j in (*JUDGES, "panel"):
        out[f"P(new>old) {j}"] = win_rate(pairs, j, new, old)
    out["fleiss_kappa sonnet~free"] = kappa(pairs, new, old)
    for j in JUDGES:
        out[f"RC {j}"] = rc(pairs, j, new, old)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.chapters.analyze")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--old", required=True, help="the earlier release's app sha")
    ap.add_argument("--new", required=True, help="the later release's app sha")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    res = analyze(collect(a.paths), a.new, a.old)
    text = json.dumps(res, indent=1)
    print(text)
    if a.json:
        a.json.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
