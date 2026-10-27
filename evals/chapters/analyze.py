"""E4: P(new release's chapter > old release's), per judge. Aggregates only.

Reads `chapters_compare` results from eval reports (files or directories),
only the last report of each batch's resume chain (refusing one whose earlier
rated outcomes changed), leaves out excluded pairs (quota, not attempted;
counted per release) and prints, for each judge (sonnet, free) and for their agreement ("panel": the
two judges' outcome when they agree, else a tie, SPEC-84's rule):

- the win rate of `--new` over `--old`, ties ½, a forfeit (a chapter one
  release did not write) a loss for that release, with a seeded bootstrap 95%
  interval resampling outline chapters: the cluster is (book, chapter), and
  every replicate and batch of that chapter moves with it, as SPEC-84's
  slot-cluster bootstrap; 2,000 resamples. `n` counts clusters, `pairs` the
  chapter pairs under them;
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


def _last_of_batch(reports: list[dict]) -> dict:
    """The tail of a batch's resume chain; every outcome an earlier report
    rated must be unchanged in it (a resume judges only unrated pairs)."""
    ids = {c.get("report_id") for c in reports}
    resumed = {r.get("resumed_from") for r in reports} - {None}
    tails = [c for c in reports if c.get("report_id") not in resumed]
    if len(tails) != 1 or (len(reports) > 1 and None in ids):
        raise SystemExit(
            f"batch {reports[0].get('batch')}: {len(reports)} compare reports that "
            "are not one resume chain"
        )
    last = tails[0]
    now = {(p.get("replicate", 1), p["position"]): p for p in last["pairs"]}
    for c in reports:
        for p in c["pairs"]:
            for name, j in (p.get("judges") or {}).items():
                was = j.get("outcome")
                got = (
                    (
                        (now.get((p.get("replicate", 1), p["position"])) or {}).get(
                            "judges"
                        )
                        or {}
                    )
                    .get(name, {})
                    .get("outcome")
                )
                if was is not None and got != was:
                    raise SystemExit(
                        f"batch {c.get('batch')}: {name}'s rated outcome at "
                        f"{(p.get('replicate', 1), p['position'])} changed on resume"
                    )
    return last


def collect(paths: list[Path]) -> list[dict]:
    """Every `chapters_compare` pair of the last report of each batch, tagged
    with its book; a pair in two batches' last reports is refused."""
    files: dict[Path, None] = {}
    for p in paths:
        for f in sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]:
            files[f.resolve()] = None
    batches: dict = defaultdict(list)
    for f in files:
        c = ((json.loads(f.read_text("utf-8")).get("steps") or {}).get(STEP) or {}).get(
            "compare"
        )
        if c:
            batches[(c["book"], c.get("batch"))].append(c)
    out, seen = [], set()
    for reports in batches.values():
        c = _last_of_batch(reports)
        for pair in c["pairs"]:
            key = (
                c["book"],
                c.get("batch"),
                pair.get("replicate", 1),
                pair["position"],
            )
            if key in seen:
                raise SystemExit(f"{key} is judged twice in one report")
            seen.add(key)
            out.append(
                pair
                | {
                    "book": c["book"],
                    "releases": c["releases"],
                    "batch": c.get("batch"),
                }
            )
            out[-1].setdefault("replicate", 1)
    return out


def value(outcome: str | None, new: str, old: str) -> float | None:
    if outcome == new:
        return 1.0
    if outcome == old:
        return 0.0
    return 0.5 if outcome == "tie" else None


def judge_outcome(pair: dict, judge: str) -> str | None:
    """The pair's outcome for a judge (or "panel"), a forfeit applied; None
    for a pair left out (not attempted, or a quota failure on either side)."""
    if pair.get("excluded"):
        return None
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


def _excluded(pairs: list[dict], new: str, old: str) -> dict:
    """Pairs left out on both sides, by reason and by the release it hit."""
    out = {}
    for reason in ("quota", "not_attempted"):
        hit = [p for p in pairs if p.get("excluded") == reason]
        who = [s for p in hit for s in (p.get(reason) or [])]
        out[reason] = {"pairs": len(hit), "new": who.count(new), "old": who.count(old)}
    return out


def win_rate(
    pairs: list[dict], judge: str, new: str, old: str, by: str = "chapter"
) -> dict:
    """`by` = "chapter" (the primary: (book, k)) or "run" (sensitivity: (book, batch))."""
    clusters, unrated, consistent, ordered = {}, 0, 0, 0
    per_book: dict = defaultdict(list)
    for p in pairs:
        if p.get("excluded"):
            continue
        v = value(judge_outcome(p, judge), new, old)
        if v is None:
            unrated += p.get("forfeit") != "both"
            continue
        cluster = p["position"] if by == "chapter" else p.get("batch")
        clusters.setdefault((p["book"], cluster), []).append(v)
        per_book[p["book"]].append(v)
        if judge in JUDGES and not p.get("forfeit"):
            ordered += 1
            consistent += p["judges"][judge]["outcome"] != "tie"
    out = bootstrap(clusters) | {
        "pairs": sum(len(v) for v in clusters.values()),
        "unrated": unrated,
        "forfeits": sum(1 for p in pairs if p.get("forfeit") not in (None, "both")),
        "both_empty": sum(1 for p in pairs if p.get("forfeit") == "both"),
        "excluded": _excluded(pairs, new, old),
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
        if p.get("forfeit") or p.get("excluded") or None in got:
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
            diffs.setdefault((p["book"], p["position"]), []).append(s[new] - s[old])
    return {
        "new_mean": round(mean(scores[new]), 4) if scores[new] else None,
        "old_mean": round(mean(scores[old]), 4) if scores[old] else None,
        "new_minus_old": bootstrap(diffs),
        "trimmed_chapters": trimmed,
    }


def power(
    n: int,
    p: float,
    ties: float,
    reps: int = 1,
    spread: float = 0.0,
    sims: int = 400,
    seed: int = 0,
    resamples: int = 500,
) -> float:
    """Share of simulated runs whose cluster-bootstrap 95% interval excludes 0.5
    upward: n clusters (outline chapters) of `reps` pairs each, a `ties` share
    of order-flipped pairs, and each chapter's own win rate drawn uniformly
    within ±`spread` of p (chapters differ; replicates of one chapter share it)."""
    if not (ties / 2 <= p - spread and p + spread <= 1 - ties / 2):
        raise ValueError("p, spread and ties are inconsistent")
    rng = random.Random(seed)
    hits = 0
    for _ in range(sims):
        clusters = []
        for _ in range(n):
            a = rng.uniform(p - spread, p + spread) - ties / 2
            w = [a, ties, 1 - ties - a]
            clusters.append(rng.choices([1.0, 0.5, 0.0], weights=w, k=reps))
        boot = sorted(
            mean(v for c in rng.choices(clusters, k=n) for v in c)
            for _ in range(resamples)
        )
        hits += boot[int(resamples * (1 - LEVEL) / 2)] > 0.5
    return hits / sims


def detectable(n: int, ties: float, reps: int, **kw) -> float | None:
    """The smallest win rate p on a 0.01 grid with power(n, p, ties, reps,
    spread=0, sims=400, seed=0) >= 0.80 (None if none up to the grid's end)."""
    grid = [round(0.51 + k / 100, 2) for k in range(49)]
    grid = [p for p in grid if p <= 1 - ties / 2]
    kw = {"spread": 0.0, "sims": 400, "seed": 0} | kw
    lo, hi, best = 0, len(grid) - 1, None
    while lo <= hi:  # power rises with p
        mid = (lo + hi) // 2
        if power(n, grid[mid], ties, reps, **kw) >= 0.80:
            best, hi = grid[mid], mid - 1
        else:
            lo = mid + 1
    return best


def decision(res: dict, mde_kw: dict | None) -> str:
    s, f = res["P(new>old) sonnet"], res["P(new>old) free"]
    flips, rated = res["sonnet_flips"]
    if rated and flips > rated / 2:
        return "undetermined: Sonnet is order-driven on these chapters"
    n_pairs = s["pairs"]
    if s["ci"] and s["ci"][0] > 0.5:
        line = f"Sonnet prefers wave 18's chapters to wave 17's (n = {n_pairs})"
    elif s["ci"] and s["ci"][1] < 0.5:
        line = f"Sonnet prefers wave 17's chapters to wave 18's (n = {n_pairs})"
    else:
        line = f"no difference detectable at n = {n_pairs} (not equivalence)"
        if mde_kw is not None and s["n"]:
            ties = flips / rated if rated else 0.0
            reps = max(1, round(n_pairs / s["n"]))
            p80 = detectable(s["n"], ties, reps, **mde_kw)
            line += f"; detectable at 80% power: p >= {p80} ({s['n']} chapters, ties {ties:.2f}, reps {reps})"
        return line
    if f["ci"] and s["p"] is not None:
        other = (f["ci"][1] < 0.5) if s["p"] > 0.5 else (f["ci"][0] > 0.5)
        if other:
            line += (
                f"; the free chain disagrees (P = {f['p']}, provider "
                f"{', '.join(res['free_providers']) or '?'}): judge-dependent"
            )
    return line


def resolve(prefix: str, pairs: list[dict]) -> str:
    """The one release sha in the reports that starts with `prefix`."""
    got = {s for p in pairs for s in p["releases"] if s.startswith(prefix)}
    if len(got) != 1:
        raise SystemExit(f"{prefix!r} names {len(got)} releases in the reports")
    return got.pop()


def analyze(pairs: list[dict], new: str, old: str, mde_kw: dict | None = None) -> dict:
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
    out["P(new>old) sonnet, by book-day run (sensitivity)"] = win_rate(
        pairs, "sonnet", new, old, by="run"
    )
    for j in JUDGES:
        out[f"RC {j}"] = rc(pairs, j, new, old)
    rated = [
        p["judges"]["sonnet"]["outcome"]
        for p in pairs
        if not p.get("excluded")
        and not p.get("forfeit")
        and (p.get("judges") or {}).get("sonnet", {}).get("outcome") is not None
    ]
    out["sonnet_flips"] = [rated.count("tie"), len(rated)]
    out["free_providers"] = sorted(
        {
            o[4]
            for p in pairs
            for o in ((p.get("judges") or {}).get("free") or {}).get("orders", [])
            if len(o) > 4 and o[4]
        }
    )
    words = {new: [], old: []}
    for p in pairs:
        for sha, w in (p.get("words") or {}).items():
            if sha in words and not p.get("excluded"):
                words[sha].append(w)
    out["words per chapter"] = {
        k: round(mean(v), 1) if v else None
        for k, v in (("new", words[new]), ("old", words[old]))
    }
    out["decision"] = decision(out, mde_kw)
    out["n"] = (
        f"{out['P(new>old) sonnet']['pairs']} pairs (Sonnet-rated plus forfeits) over {out['P(new>old) sonnet']['n']} outline chapters"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.chapters.analyze")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--old", required=True, help="the earlier release's app sha")
    ap.add_argument("--new", required=True, help="the later release's app sha")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    res = analyze(collect(a.paths), a.new, a.old, mde_kw={})
    text = json.dumps(res, indent=1)
    print(text)
    if a.json:
        a.json.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
