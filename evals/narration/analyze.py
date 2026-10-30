"""SPEC-96: the pre-registered analysis of wave 21's arms. Run once, after the
last pair (SPEC-96 Conduct); before then only counts are read.

Inputs: the `chapters` reports of every arm run named in the ledger (M1 is
computed from their text by evals.narration.contract) and the
`narration_judges` reports judging them (M2, M3). A run's arm is its report's
`ablation` ("" A, `pov_fix` P, `no_excerpts` N, `no_voices` V). A run's day is
the Pacific date of its creation: from `--dates` (JSON {run id: "YYYY-MM-DD"},
written by the `dates` command from each run's GitHub `created_at`), else the
Pacific date of the `chapters` step's `started_at`.

Pairs. For each contrast (P, N, V vs A), each treatment chapter is paired with
A's chapter of the same book, outline position, replicate and day (A's run of
that book on that day; two such A runs are refused). A pair is left out when
either side's chapter has a quota failure or no scenes (counted per arm), when
no A chapter matches (unpaired, counted), or when the two runs' outline
digests differ (counted). A chapter with no tagged dialogue line is left out
of M3 only, a chapter with no judged narrator passage (its POV has no voice
source) out of M2 only, both counted.

Statistic (pre-registered). Per contrast and measure: the mean paired
difference (treatment - A) over the chapter pairs; its 95% percentile
bootstrap interval, RESAMPLES = 10,000 resamples with seed 0, resampling
outline chapters (book, position) with all their pairs (every replicate and
day of that chapter moves with it); the bootstrap p = 2 x the share of
resamples on the far side of 0 (at or beyond 0, away from the observed mean;
1 when the mean is 0), capped at 1. Holm's correction at family-wise 0.05
across the primary tests that are tested (P: M1 `person_mismatch_window`;
N: M2 by Sonnet; V: M3 by Sonnet). The family is fixed before arm data: a
primary test whose measure failed its validation (`--m1-validation`,
`--m3-validation`) is removed from it and reported descriptively, and H3 is
removed (descriptive) unless A and V each have >= 60 eligible M3 lines
across their paired chapters (`eligible_lines`). A hypothesis holds when its
Holm-adjusted p is <= 0.05 and its difference is below 0 (H1: P lowers
person mismatch; H2: N lowers M2 accuracy; H3: V lowers M3 accuracy).
`tense_mismatch`, sentence-level `person_mismatch` and `quote_mismatch` are
descriptive only. The free judge's (Groq's) results are printed beside
Sonnet's and change no decision.

M2 per chapter: correct / rated passages; M3 per chapter: correct / rated
lines; M1: the chapter's mismatch shares (None when nothing classifiable).

python -m evals.narration.analyze dates RUN_ID... --repo OWNER/REPO --out F
python -m evals.narration.analyze run REPORT_OR_DIR... [--dates F]
    [--m1-validation F] [--m3-validation F] [--p-measure M] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from evals.narration import contract
from evals.narration.judges import STEP as JUDGES_STEP

RESAMPLES, LEVEL, SEED, ALPHA = 10_000, 0.95, 0, 0.05
PACIFIC = ZoneInfo("America/Los_Angeles")
ARMS = {"": "A", "pov_fix": "P", "no_excerpts": "N", "no_voices": "V"}
CONTRASTS = ("P", "N", "V")
# contrast -> (measure, judge) of its primary test (None: a script measure)
PRIMARY = {
    "P": ("person_mismatch_window", None),
    "N": ("m2", "sonnet"),
    "V": ("m3", "sonnet"),
}
M1 = ("person_mismatch", "person_mismatch_window", "tense_mismatch", "quote_mismatch")
JUDGES = ("sonnet", "free")
CAVEAT = "exploratory; Flash-Lite-written, {judged}not human, development corpora"


# --- days ----------------------------------------------------------------------


def pacific_day(stamp: str | None) -> str | None:
    """The Pacific date of an ISO timestamp ("...Z" or with an offset)."""
    if not stamp:
        return None
    t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return t.astimezone(PACIFIC).date().isoformat()


def dates(run_ids: list[str], repo: str, gh=None) -> dict[str, str]:
    """{run id: Pacific date of its GitHub `created_at`} by `gh api`."""

    def _gh(rid: str) -> str:
        return subprocess.run(
            ["gh", "api", f"repos/{repo}/actions/runs/{rid}", "--jq", ".created_at"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    gh = gh or _gh
    return {rid: pacific_day(gh(rid)) for rid in run_ids}


# --- collecting -----------------------------------------------------------------


def judged(paths: list[Path]) -> dict[tuple, dict]:
    """{(run, position, replicate): the chapter's narration_judges record};
    a chapter judged in two reports keeps the record with more rated items
    (a stopped dispatch judged again), never a mix."""
    out: dict = {}

    def rated(rec: dict) -> int:
        return sum(
            t.get("rated", 0)
            for part in ("m2_judges", "m3")
            for t in (rec.get(part) or {}).values()
        )

    for f in contract.report_files(paths):
        res = (json.loads(f.read_text("utf-8")).get("steps") or {}).get(JUDGES_STEP)
        n = (res or {}).get("narration")
        if not n:
            continue
        for rec in n["chapters"]:
            key = (str(rec["run"]), rec["position"], rec.get("replicate", 1))
            if key not in out or rated(rec) > rated(out[key]):
                out[key] = rec
    return out


def _share(t: dict | None) -> float | None:
    if not t or not t.get("rated"):
        return None
    return t["correct"] / t["rated"]


def chapters(paths: list[Path], days: dict | None = None) -> list[dict]:
    """One row per arm chapter: arm, book, position, replicate, day, failure,
    scenes, M1 shares, and M2/M3 per judge."""
    days = {str(k): v for k, v in (days or {}).items()}
    js = judged(paths)
    rows = []
    for r in contract.score_reports(paths):
        arm = ARMS.get(r["ablation"])
        if arm is None:
            raise SystemExit(f"run {r['run']}: unknown ablation {r['ablation']!r}")
        key = (str(r["run"]), r["position"], r.get("replicate", 1))
        rec = js.get(key) or {}
        row = {
            "run": str(r["run"]),
            "arm": arm,
            "book": r["book"],
            "position": r["position"],
            "replicate": r.get("replicate", 1),
            "day": days.get(str(r["run"])) or pacific_day(r.get("started_at")),
            "outline_digest": r.get("outline_digest"),
            "app_sha": r.get("app_sha"),
            "failure": r.get("failure"),
            "scenes": r.get("scenes"),
            "judged": bool(rec),
        }
        row |= {m: r[m] for m in M1}
        for j in JUDGES:
            row[f"m2 {j}"] = _share((rec.get("m2_judges") or {}).get(j))
            row[f"m3 {j}"] = _share((rec.get("m3") or {}).get(j))
        row["m3_lines"] = rec.get("m3_lines")
        rows.append(row)
    return rows


def excluded(row: dict) -> str | None:
    if row["failure"] == "quota":
        return "quota"
    if not row["scenes"]:
        return "no_scenes"
    return None


def pairs(rows: list[dict], contrast: str) -> tuple[list[tuple], dict]:
    """[(treatment row, A row)] for `contrast`, and the counts left out."""
    a_runs: dict = defaultdict(set)
    for r in rows:
        if r["arm"] == "A":
            a_runs[(r["book"], r["day"])].add(r["run"])
    for (book, day), runs in a_runs.items():
        if len(runs) > 1 and day is not None:
            raise SystemExit(f"{book} on {day}: {len(runs)} A runs {sorted(runs)}")
    a = {
        (r["book"], r["position"], r["replicate"], r["day"]): r
        for r in rows
        if r["arm"] == "A" and r["day"] is not None
    }
    counts = {
        "pairs": 0,
        "unpaired": 0,
        "no_day": 0,
        "outline_differs": 0,
        "excluded": {"treatment": defaultdict(int), "A": defaultdict(int)},
    }
    out = []
    for t in rows:
        if t["arm"] != contrast:
            continue
        if t["day"] is None:
            counts["no_day"] += 1
            continue
        c = a.get((t["book"], t["position"], t["replicate"], t["day"]))
        if c is None:
            counts["unpaired"] += 1
            continue
        if t["outline_digest"] != c["outline_digest"]:
            counts["outline_differs"] += 1
            continue
        why = {"treatment": excluded(t), "A": excluded(c)}
        if any(why.values()):
            for side, w in why.items():
                if w:
                    counts["excluded"][side][w] += 1
            continue
        out.append((t, c))
    counts["pairs"] = len(out)
    counts["excluded"] = {k: dict(v) for k, v in counts["excluded"].items()}
    return out, counts


def differences(prs: list[tuple], measure: str) -> tuple[dict, int]:
    """({(book, position): [treatment - A]}, pairs left out for a missing value)."""
    clusters: dict = defaultdict(list)
    missing = 0
    for t, c in prs:
        if t[measure] is None or c[measure] is None:
            missing += 1
            continue
        clusters[(t["book"], t["position"])].append(t[measure] - c[measure])
    return dict(clusters), missing


# --- the statistic --------------------------------------------------------------


def bootstrap(
    clusters: dict, resamples: int = RESAMPLES, seed: int = SEED, level: float = LEVEL
) -> dict:
    """Mean of every pair's difference; a percentile interval and a two-sided
    p from resampling clusters with all their pairs."""
    keys = sorted(clusters)
    if not keys:
        return {"d": None, "ci": None, "p": None, "n_clusters": 0, "n_pairs": 0}
    stat = lambda ks: mean(v for k in ks for v in clusters[k])  # noqa: E731
    d = stat(keys)
    rng = random.Random(seed)
    boot = sorted(stat(rng.choices(keys, k=len(keys))) for _ in range(resamples))
    lo = int(resamples * (1 - level) / 2)
    if d > 0:
        far = sum(b <= 0 for b in boot)
    elif d < 0:
        far = sum(b >= 0 for b in boot)
    else:
        far = resamples / 2
    return {
        "d": round(d, 4),
        "ci": [round(boot[lo], 4), round(boot[-lo - 1], 4)],
        "p": min(1.0, round(2 * far / resamples, 6)),
        "n_clusters": len(keys),
        "n_pairs": sum(len(clusters[k]) for k in keys),
    }


H3_MIN_LINES = 60  # H3 is tested only when A and V each have this many


def eligible_lines(prs: list[tuple]) -> dict:
    """M3's eligible (tag-fixed) dialogue lines summed over the paired
    chapters of a contrast, per side: {"treatment": n, "A": n}. The
    pre-registration tests H3 only when both are >= H3_MIN_LINES."""
    return {
        "treatment": sum(t.get("m3_lines") or 0 for t, _ in prs),
        "A": sum(c.get("m3_lines") or 0 for _, c in prs),
    }


def holm(ps: dict[str, float | None], alpha: float = ALPHA) -> dict:
    """{name: {"p_holm", "reject"}}: Holm's step-down adjustment over the
    family's p-values (a missing p counts as 1, staying in the family)."""
    items = sorted(ps.items(), key=lambda kv: (1.0 if kv[1] is None else kv[1], kv[0]))
    m, out, running, stop = len(items), {}, 0.0, False
    for i, (name, p) in enumerate(items):
        p = 1.0 if p is None else p
        running = max(running, min(1.0, (m - i) * p))
        stop = stop or p > alpha / (m - i)
        out[name] = {"p_holm": round(running, 6), "reject": not stop}
    return out


def power(
    n: int,
    effect: float,
    sd: float,
    reps: int = 1,
    spread: float = 0.0,
    alpha: float = ALPHA / 3,
    sims: int = 400,
    seed: int = 0,
    resamples: int = 500,
) -> float:
    """Share of simulated studies whose bootstrap p (this module's statistic,
    `resamples` resamples) is <= `alpha` with the observed difference on the
    effect's side: n outline chapters of `reps` pairs each, each pair's
    difference ~ Normal(chapter mean, sd), each chapter's mean drawn uniformly
    within ±`spread` of `effect`. The default alpha is Holm's first step
    (0.05 / 3), the bar the smallest p meets."""
    rng = random.Random(seed)
    hits = 0
    for k in range(sims):
        clusters = {}
        for c in range(n):
            mu = rng.uniform(effect - spread, effect + spread)
            clusters[c] = [rng.gauss(mu, sd) for _ in range(reps)]
        b = bootstrap(clusters, resamples=resamples, seed=seed + k)
        hits += b["p"] <= alpha and (b["d"] or 0) * effect > 0
    return hits / sims


def detectable(n: int, sd: float, reps: int = 1, **kw) -> float | None:
    """The smallest |effect| on a 0.01 grid up to 1 with power >= 0.80."""
    grid = [round(k / 100, 2) for k in range(1, 101)]
    lo, hi, best = 0, len(grid) - 1, None
    while lo <= hi:  # power rises with the effect
        mid = (lo + hi) // 2
        if power(n, -grid[mid], sd, reps, **kw) >= 0.80:
            best, hi = grid[mid], mid - 1
        else:
            lo = mid + 1
    return best


# --- the analysis ---------------------------------------------------------------

_WORDING = {
    "P": (
        "dropping the default POV mode lowers person mismatch by {d}",
        "no effect of dropping the default POV mode detectable at n = {n} "
        "(not equivalence)",
    ),
    "N": (
        "the book's narration excerpts carry the narrator's voice: without "
        "them M2 accuracy is lower by {d}",
        "no effect of the excerpts detectable at n = {n} (not equivalence)",
    ),
    "V": (
        "the VOICES rule makes speakers recognisable: without it M3 accuracy "
        "is lower by {d}",
        "no effect of the VOICES rule detectable at n = {n} (not equivalence)",
    ),
}
_FOLLOW = {
    "P": ("the fix is proposed for the next product wave", None),
    "N": (
        None,
        "the excerpts become a candidate for removal, decided with E4's result",
    ),
    "V": (
        None,
        "the VOICES rule becomes a candidate for removal, decided with E4's result",
    ),
}


def _primary_key(contrast: str, primary: dict = PRIMARY) -> str:
    measure, judge = primary[contrast]
    return measure if judge is None else f"{measure} {judge}"


def decision(
    contrast: str,
    res: dict,
    test: dict,
    untested: str | None,
    primary: dict = PRIMARY,
) -> str:
    key = _primary_key(contrast, primary)
    s = res[key]
    judge = primary[contrast][1]
    caveat = CAVEAT.format(judged="Sonnet-judged, " if judge else "")
    if untested:
        return (
            f"{contrast}: {key} is not tested ({untested}): descriptive only "
            f"(d = {s['d']}, n = {s['n_pairs']}); {caveat}"
        )
    holds = test["reject"] and s["d"] is not None and s["d"] < 0
    yes, no = _WORDING[contrast]
    follow = _FOLLOW[contrast][0 if holds else 1]
    line = (yes if holds else no).format(d=abs(s["d"] or 0), n=s["n_pairs"])
    if not holds and test["reject"]:
        line = (
            f"the difference is the other way (d = {s['d']}, Holm p = "
            f"{test['p_holm']}): the hypothesis does not hold"
        )
    return f"{contrast}: {line}" + (f"; {follow}" if follow else "") + f" ({caveat})"


def analyze(
    rows: list[dict],
    validated: dict | None = None,
    resamples: int = RESAMPLES,
    p_measure: str = PRIMARY["P"][0],
) -> dict:
    """Every contrast's measures, the Holm family and the decisions.

    The Holm family is the primary tests that are tested: a primary measure
    that failed its validation (`validated`, from the validation flags) is
    removed from the family and reported descriptively, and so is H3 when A
    or V has fewer than H3_MIN_LINES eligible M3 lines across the paired
    chapters (`eligible_lines`). `p_measure` is H1's M1 measure
    (`person_mismatch_window`, the pre-registration's choice)."""
    if p_measure not in M1:
        raise SystemExit(f"H1's measure must be one of {M1}, not {p_measure!r}")
    primary = PRIMARY | {"P": (p_measure, None)}
    validated = {"P": True, "N": True, "V": True} | (validated or {})
    measures = list(M1) + [f"{m} {j}" for m in ("m2", "m3") for j in JUDGES]
    out: dict = {
        "label": "W21, SPEC-96; "
        + CAVEAT.format(judged="judge measures Sonnet-judged, "),
        "chapters_per_arm": {
            a: sum(1 for r in rows if r["arm"] == a) for a in ("A", *CONTRASTS)
        },
        "judged_per_arm": {
            a: sum(1 for r in rows if r["arm"] == a and r["judged"])
            for a in ("A", *CONTRASTS)
        },
        "contrasts": {},
    }
    untested: dict = {}
    for contrast in CONTRASTS:
        prs, counts = pairs(rows, contrast)
        res: dict = {"counts": counts}
        for m in measures:
            clusters, missing = differences(prs, m)
            res[m] = bootstrap(clusters, resamples) | {"left_out": missing}
        res["counts"]["m3_no_line"] = res["m3 sonnet"]["left_out"]
        res["counts"]["m2_not_judged"] = res["m2 sonnet"]["left_out"]
        res["counts"]["m3_eligible_lines"] = lines = eligible_lines(prs)
        out["contrasts"][contrast] = res
        if not validated[contrast]:
            untested[contrast] = "its measure failed validation"
        elif contrast == "V" and min(lines.values()) < H3_MIN_LINES:
            untested[contrast] = (
                f"eligible M3 lines A {lines['A']}, V {lines['treatment']}; "
                f"each needs >= {H3_MIN_LINES}"
            )
    keys = {c: _primary_key(c, primary) for c in CONTRASTS}
    family = [c for c in CONTRASTS if c not in untested]
    tests = holm({c: out["contrasts"][c][keys[c]]["p"] for c in family})
    none = {"p_holm": None, "reject": False}
    out["family"] = family
    out["primary"] = {
        c: {
            "measure": keys[c],
            "validated": validated[c],
            "tested": c in family,
            "untested_because": untested.get(c),
        }
        | out["contrasts"][c][keys[c]]
        | tests.get(c, none)
        for c in CONTRASTS
    }
    out["decisions"] = [
        decision(c, out["contrasts"][c], tests.get(c, none), untested.get(c), primary)
        for c in CONTRASTS
    ]
    return out


def render(res: dict) -> str:
    lines = [res["label"], f"chapters per arm {res['chapters_per_arm']}"]
    lines.append(
        f"  {'contrast':8s} {'measure':24s} {'d (T-A)':>8s} {'95% CI':>18s} "
        f"{'p':>8s} {'pairs':>5s} {'clusters':>8s}"
    )
    for c, r in res["contrasts"].items():
        for m, s in r.items():
            if m == "counts":
                continue
            ci = f"[{s['ci'][0]:.3f}, {s['ci'][1]:.3f}]" if s["ci"] else "n/a"
            mark = " *" if m == res["primary"][c]["measure"] else ""
            lines.append(
                f"  {c:8s} {m + mark:24s} {s['d']!s:>8s} {ci:>18s} {s['p']!s:>8s} "
                f"{s['n_pairs']:5d} {s['n_clusters']:8d}"
            )
        lines.append(f"  {c:8s} counts {json.dumps(r['counts'])}")
    lines.append("primary tests (* above), Holm across the three:")
    for c, p in res["primary"].items():
        lines.append(
            f"  {c}: {p['measure']} d = {p['d']} p = {p['p']} Holm p = "
            f"{p['p_holm']} reject = {p['reject']} tested = {p['tested']}"
            + (f" ({p['untested_because']})" if p["untested_because"] else "")
        )
    lines.append("decisions:")
    lines += [f"  {d}" for d in res["decisions"]]
    return "\n".join(lines)


def _validated(m1: Path | None, m3: Path | None, p_measure: str) -> dict:
    out = {}
    if m1:
        summary = json.loads(m1.read_text("utf-8"))["summary"]
        out["P"] = bool(summary.get(f"validated {p_measure}"))
    if m3:
        out["V"] = bool(json.loads(m3.read_text("utf-8")).get("m3_validated"))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dates")
    d.add_argument("runs", nargs="+")
    d.add_argument("--repo", required=True)
    d.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("reports", type=Path, nargs="+")
    r.add_argument("--dates", type=Path)
    r.add_argument("--m1-validation", type=Path)
    r.add_argument("--m3-validation", type=Path)
    r.add_argument("--resamples", type=int, default=RESAMPLES)
    r.add_argument("--p-measure", default=PRIMARY["P"][0], help="H1's M1 measure")
    r.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "dates":
        got = dates(a.runs, a.repo)
        a.out.write_text(json.dumps(got, indent=1))
        print(json.dumps(got))
        return
    days = json.loads(a.dates.read_text("utf-8")) if a.dates else None
    rows = chapters(a.reports, days)
    ok = _validated(a.m1_validation, a.m3_validation, a.p_measure)
    res = analyze(rows, ok, a.resamples, a.p_measure)
    print(render(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
