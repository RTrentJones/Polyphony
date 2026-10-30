"""SPEC-96: the pre-registered analysis of wave 21's arms. Run once, after the
last pair (SPEC-96 Conduct); before then only counts are read.

Inputs (all required, refused rather than guessed):
- the `chapters` reports of every arm run named in the ledger (M1 is
  computed from their text by evals.narration.contract) and the
  `narration_judges` reports judging them (M2, M3);
- `--app-sha`: the app every arm ran; a chapter whose report names another
  app sha is refused;
- `--days`: JSON {run id: ledger day label, e.g. "W21 day 3"}; a run
  without a label is refused.
A run's arm is its report's top-level `ablation` (None/"" A, `pov_fix` P,
`no_excerpts` N, `no_voices` V); a report without the key (E4's) is refused
(`contract.ablation`).

Pairs. For each contrast (P, N, V vs A), each treatment chapter is paired with
A's chapter of the same book, outline position and replicate on the same
ledger day (A's run of that book that day; two such A runs are refused). A
pair is left out when either side's chapter has a quota failure or no scenes
(counted per arm), when no A chapter matches (unpaired), or when the two
runs' outline digests differ. A chapter with no tagged dialogue line is left
out of M3 only, one with no judged narrator passage out of M2 only, counted.

Primary statistic (`test`). Per contrast and measure, the pairs' differences
(treatment - A) are averaged within each cluster, an outline chapter (book,
position) with all its pairs, and the k cluster means are the units:
- d = the mean of the cluster means;
- p = an exact two-sided sign-flip randomization test on the cluster means:
  the share of the 2^k sign patterns whose |mean| is at least the observed
  |d| (enumerated exactly for k <= 20, by meet in the middle; else
  SIGN_DRAWS = 100,000 seeded draws, seed 0, p = (hits + 1) / (draws + 1));
- a 95% t-interval on the cluster means (df = k - 1).
The cluster percentile bootstrap (10,000 resamples, seed 0) is kept as a
descriptive line only: at these cluster counts its null rejection rate at a
nominal 0.05 was measured at 0.08-0.135.

Family. Holm at family-wise 0.05 on the sign-flip p over H1 (P: M1
`person_mismatch_window`) and H2 (N: M2 by Sonnet), minus any whose measure
failed its validation (`--m1-validation`; `--m2-...` has no bar). H3 (V: M3
by Sonnet) is descriptive only. A hypothesis holds when its Holm-adjusted p
is <= 0.05 and its difference is below 0.

Instrument floor (read at analysis, from A's chapters in the contrast's
pairs, per cluster): when the t-interval of A's M2 accuracy includes 0.5, H2
reads "undetermined: instrument at floor on generated text" (no removal
wording); when the t-interval's lower bound of A's M3 accuracy is not above
chance + 0.20 (chance: 1 / the listed cast's size, averaged over the eligible
lines), M3 reads the same.

Sensitivities (descriptive, beside the primaries): (a) H1 on sentence-level
`person_mismatch`, and whether its direction agrees; (b) N vs A on
`person_mismatch_window`; (c) H2 on the pairs whose chapters are both first
person (`judges.first_person`); (d) M3 without the lines a first-person tag
credited to the narrator.

Per arm: chapter outcomes (failure class: none, product, quota; chapters
with no scenes) as an outcome, not only an exclusion; M3 eligible lines; and
a descriptive 8-gram overlap between each chapter's text and the voice
samples M2's judge sees for it (the share of the chapter's word 8-grams that
occur in the samples).

The free judge's (Groq's) results are printed beside Sonnet's and change no
decision. `tense_mismatch`, `quote_mismatch` and sentence-level
`person_mismatch` are descriptive only.

python -m evals.narration.analyze dates RUN_ID... --repo OWNER/REPO
python -m evals.narration.analyze run REPORT_OR_DIR... --app-sha SHA
    --days DAYS.json [--m1-validation F] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev

from evals.narration import contract
from evals.narration.judges import STEP as JUDGES_STEP

RESAMPLES, LEVEL, SEED, ALPHA = 10_000, 0.95, 0, 0.05
EXACT_MAX, SIGN_DRAWS = 20, 100_000
NGRAM = 8
ARMS = {"": "A", "pov_fix": "P", "no_excerpts": "N", "no_voices": "V"}
CONTRASTS = ("P", "N", "V")
# contrast -> (measure, judge): the tested primaries, and H3's (descriptive)
PRIMARY = {"P": ("person_mismatch_window", None), "N": ("m2", "sonnet")}
DESCRIPTIVE = {"V": ("m3", "sonnet")}
M1 = ("person_mismatch", "person_mismatch_window", "tense_mismatch", "quote_mismatch")
JUDGES = ("sonnet", "free")
CHANCE_MARGIN = 0.20
FLOOR = "undetermined: instrument at floor on generated text"
CAVEAT = "exploratory; Flash-Lite-written, {judged}not human, development corpora"
# two-sided 95% t critical values; df beyond the table interpolates in 1/df
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000,
    120: 1.980,
}  # fmt: skip


# --- days -----------------------------------------------------------------------


def dates(run_ids: list[str], repo: str, gh=None) -> dict[str, str]:
    """{run id: its GitHub `created_at`}, a convenience for writing the
    ledger's day labels (`--days`); the analysis never reads dates."""

    def _gh(rid: str) -> str:
        return subprocess.run(
            ["gh", "api", f"repos/{repo}/actions/runs/{rid}", "--jq", ".created_at"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    gh = gh or _gh
    return {rid: gh(rid) for rid in run_ids}


# --- collecting -----------------------------------------------------------------


def judged(paths: list[Path]) -> dict[tuple, dict]:
    """{(run, book, position, replicate): the chapter's narration_judges
    record} (a run may carry several books, so the book is part of the key);
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
        report = json.loads(f.read_text("utf-8"))
        res = (report.get("steps") or {}).get(JUDGES_STEP)
        n = (res or {}).get("narration")
        if not n:
            continue
        for rec in n["chapters"]:
            book = rec.get("book") or n.get("book") or report.get("book")
            key = (str(rec["run"]), book, rec["position"], rec.get("replicate", 1))
            if key not in out or rated(rec) > rated(out[key]):
                out[key] = rec
    return out


def _share(t: dict | None, own: bool = False) -> float | None:
    rated, correct = ("rated_own", "correct_own") if own else ("rated", "correct")
    if not t or not t.get(rated):
        return None
    return t[correct] / t[rated]


def _grams(text: str, n: int = NGRAM) -> set:
    w = re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def sample_overlap(book: str, row: dict) -> float | None:
    """The share of the chapter's word 8-grams found in the voice samples
    M2's judge sees for it (both narrators'); None without such samples."""
    from evals.narration import judges
    from evals.validation import rn2

    texts = [t for t, _ in judges.scene_texts(row)]
    pair = judges.narrator_pair(book, row.get("pov"))
    passages = judges.passages(texts)
    if pair is None or not passages:
        return None
    samples = [s for who in pair for s in rn2.voice_samples(book, who, passages)]
    mine = _grams("\n".join(texts))
    if not mine:
        return None
    return len(mine & _grams("\n".join(samples))) / len(mine)


def chapters(paths: list[Path], days: dict, app_sha: str) -> list[dict]:
    """One row per arm chapter: arm, book, position, replicate, day label,
    failure, scenes, M1, first person, sample overlap, M2/M3 per judge."""
    from evals.narration.judges import first_person

    days = {str(k): v for k, v in (days or {}).items()}
    js = judged(paths)
    profiles: dict = {}
    rows = []
    for meta, r in contract.chapter_rows(paths):
        run = str(meta["run"])
        if meta["app_sha"] != app_sha:
            raise SystemExit(f"run {run}: app {meta['app_sha']}, not {app_sha}")
        if run not in days:
            raise SystemExit(f"run {run} has no ledger day label in --days")
        arm = ARMS.get(meta["ablation"])
        if arm is None:
            raise SystemExit(f"run {run}: unknown ablation {meta['ablation']!r}")
        book = meta["book"]
        if book not in profiles:
            profiles[book] = contract.profile(book)
        text = contract.chapter_text(r)
        m1 = contract.score_text(text, profiles[book])
        rec = (
            js.get((run, meta["book"], r.get("position"), r.get("replicate", 1))) or {}
        )
        row = {
            "run": run,
            "arm": arm,
            "book": book,
            "position": r.get("position"),
            "replicate": r.get("replicate", 1),
            "day": days[run],
            "outline_digest": meta["outline_digest"],
            "failure": r.get("failure"),
            "scenes": sum(
                1 for s in r.get("scenes") or [] if (s.get("content") or "").strip()
            ),
            "judged": bool(rec.get("judged")),
            "first_person": bool(text.strip()) and first_person(text),
            "sample_overlap": sample_overlap(book, r) if text.strip() else None,
        }
        row |= {m: m1[m] for m in M1}
        for j in JUDGES:
            row[f"m2 {j}"] = _share((rec.get("m2_judges") or {}).get(j))
            row[f"m3 {j}"] = _share((rec.get("m3") or {}).get(j))
            row[f"m3_own {j}"] = _share((rec.get("m3") or {}).get(j), own=True)
        row["m3_lines"] = rec.get("m3_lines")
        row["m3_narrator_lines"] = rec.get("m3_narrator_lines")
        row["m3_chance_sum"] = rec.get("m3_chance_sum")
        rows.append(row)
    return rows


def excluded(row: dict) -> str | None:
    if row["failure"] == "quota":
        return "quota"
    if not row["scenes"]:
        return "no_scenes"
    return None


def pairs(rows: list[dict], contrast: str) -> tuple[list[tuple], dict]:
    """[(treatment row, A row)] for `contrast` by (book, day label, position,
    replicate), and the counts left out."""
    a_runs: dict = defaultdict(set)
    for r in rows:
        if r["arm"] == "A":
            a_runs[(r["book"], r["day"])].add(r["run"])
    for (book, day), runs in a_runs.items():
        if len(runs) > 1:
            raise SystemExit(f"{book} on {day}: {len(runs)} A runs {sorted(runs)}")
    a = {
        (r["book"], r["day"], r["position"], r["replicate"]): r
        for r in rows
        if r["arm"] == "A"
    }
    counts = {
        "pairs": 0,
        "unpaired": 0,
        "outline_differs": 0,
        "excluded": {"treatment": defaultdict(int), "A": defaultdict(int)},
    }
    out = []
    for t in rows:
        if t["arm"] != contrast:
            continue
        c = a.get((t["book"], t["day"], t["position"], t["replicate"]))
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
    # An incomplete day-table cycle leaves books unevenly paired: show it.
    counts["pairs_by_book"] = dict(sorted(Counter(t["book"] for t, _ in out).items()))
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


def eligible_lines(prs: list[tuple]) -> dict:
    """M3's eligible (tag-fixed) dialogue lines summed over the paired
    chapters of a contrast, per side: {"treatment": n, "A": n}."""
    return {
        "treatment": sum(t.get("m3_lines") or 0 for t, _ in prs),
        "A": sum(c.get("m3_lines") or 0 for _, c in prs),
    }


# --- the statistics ---------------------------------------------------------------


def _subset_sums(xs: list[float]) -> list[float]:
    sums = [0.0]
    for x in xs:
        sums = [s + x for s in sums] + [s - x for s in sums]
    return sums


def sign_flip_p(
    means: list[float], draws: int = SIGN_DRAWS, seed: int = SEED
) -> float | None:
    """Two-sided sign-flip randomization p on cluster means: the share of
    sign patterns whose |sum| reaches the observed |sum|. Exact for k <=
    EXACT_MAX (meet in the middle: the two halves' 2^(k/2) signed sums,
    one sorted and bisected); else `draws` seeded patterns, (hits+1)/(draws+1)."""
    k = len(means)
    if not k:
        return None
    obs = abs(sum(means))
    eps = 1e-9 * max(1.0, sum(abs(m) for m in means))
    if obs <= eps:
        return 1.0
    if k <= EXACT_MAX:
        left = _subset_sums(means[: k // 2])
        right = sorted(_subset_sums(means[k // 2 :]))
        n, hits = len(right), 0
        for a in left:
            hits += n - bisect_left(right, obs - a - eps)
            hits += bisect_right(right, -obs - a + eps)
        return min(1.0, hits / 2**k)
    rng = random.Random(seed)
    hits = sum(
        abs(sum(m if rng.random() < 0.5 else -m for m in means)) >= obs - eps
        for _ in range(draws)
    )
    return (hits + 1) / (draws + 1)


def t_crit(df: int) -> float:
    """The two-sided 95% t critical value (table; 1/df interpolation)."""
    if df in _T975:
        return _T975[df]
    keys = sorted(_T975) + [float("inf")]
    vals = [_T975[k] for k in sorted(_T975)] + [1.960]
    for lo, hi, vlo, vhi in zip(keys, keys[1:], vals, vals[1:]):
        if lo < df < hi:
            x, xl, xh = 1 / df, 1 / lo, (0.0 if hi == float("inf") else 1 / hi)
            return vhi + (vlo - vhi) * (x - xh) / (xl - xh)
    return 1.960


def t_interval(xs: list[float]) -> list[float] | None:
    """The 95% t-interval of the mean of `xs` (df = len - 1)."""
    if len(xs) < 2:
        return None
    m, half = mean(xs), t_crit(len(xs) - 1) * stdev(xs) / len(xs) ** 0.5
    return [round(m - half, 4), round(m + half, 4)]


def bootstrap(
    clusters: dict, resamples: int = RESAMPLES, seed: int = SEED, level: float = LEVEL
) -> dict:
    """Descriptive only: the pooled mean of the pairs, its cluster percentile
    interval and bootstrap p (2 x the far-side share, capped at 1)."""
    keys = sorted(clusters)
    if not keys:
        return {"d": None, "ci": None, "p": None}
    stat = lambda ks: mean(v for k in ks for v in clusters[k])  # noqa: E731
    d = stat(keys)
    rng = random.Random(seed)
    boot = sorted(stat(rng.choices(keys, k=len(keys))) for _ in range(resamples))
    lo = int(resamples * (1 - level) / 2)
    far = (
        sum(b <= 0 for b in boot)
        if d > 0
        else sum(b >= 0 for b in boot) if d < 0 else resamples / 2
    )
    return {
        "d": round(d, 4),
        "ci": [round(boot[lo], 4), round(boot[-lo - 1], 4)],
        "p": min(1.0, round(2 * far / resamples, 6)),
    }


def test(clusters: dict, resamples: int = RESAMPLES) -> dict:
    """The primary statistic on {cluster: [differences]} (module docstring),
    with the bootstrap beside it, descriptive."""
    keys = sorted(clusters)
    means = [mean(clusters[k]) for k in keys]
    return {
        "d": round(mean(means), 4) if means else None,
        "p": sign_flip_p(means),
        "t_ci": t_interval(means),
        "exact": len(means) <= EXACT_MAX,
        "n_clusters": len(keys),
        "n_pairs": sum(len(clusters[k]) for k in keys),
        "bootstrap (descriptive)": bootstrap(clusters, resamples),
    }


def holm(ps: dict[str, float | None], alpha: float = ALPHA) -> dict:
    """{name: {"p_holm", "reject"}}: Holm's step-down adjustment over the
    family's p-values (a missing p counts as 1)."""
    items = sorted(ps.items(), key=lambda kv: (1.0 if kv[1] is None else kv[1], kv[0]))
    m, out, running, stop = len(items), {}, 0.0, False
    for i, (name, p) in enumerate(items):
        p = 1.0 if p is None else p
        running = max(running, min(1.0, (m - i) * p))
        stop = stop or p > alpha / (m - i)
        out[name] = {"p_holm": round(running, 6), "reject": not stop}
    return out


def power(
    k: int,
    effect: float,
    sd: float,
    reps: int = 1,
    alpha: float = ALPHA / 2,
    sims: int = 400,
    seed: int = 0,
) -> float:
    """Share of simulated studies the primary test (sign-flip on cluster
    means) rejects at `alpha` with the difference on the effect's side: k
    clusters of `reps` pairs, each pair's difference ~ Normal(effect, sd).
    The default alpha is Holm's first step over the two tested primaries.
    With effect 0 it is the test's null rejection rate (either side)."""
    rng = random.Random(seed)
    hits = 0
    for _ in range(sims):
        means = [mean(rng.gauss(effect, sd) for _ in range(reps)) for _ in range(k)]
        p = sign_flip_p(means)
        side = effect == 0 or mean(means) * effect > 0  # 0: the null's size
        hits += p is not None and p <= alpha and side
    return hits / sims


# --- the analysis -------------------------------------------------------------------

_WORDING = {
    "P": (
        "dropping the default POV mode lowers person mismatch by {d}",
        "no effect of dropping the default POV mode detectable at n = {n} "
        "(not equivalence)",
    ),
    "N": (
        "without the book-narration excerpts, the judge attributes the narration "
        "to the planned narrator less often by {d}",
        "no effect of the excerpts on narrator attribution detectable at n = {n} "
        "(not equivalence)",
    ),
}
_FOLLOW = {
    "P": ("the fix is proposed for the next product wave", None),
    "N": (
        None,
        "the lever becomes a candidate for removal, decided with E4's result",
    ),
}
# H2 the other way round (N attributes better than A), as pre-registered.
_REVERSED = {
    "N": "the excerpts mislead attribution (d = {d}); the excerpt selection is "
    "revisited in the next product wave",
}
# An exact sign-flip test on k clusters cannot give p below 2/2^k, which must
# reach Holm's first step (0.025): fewer clusters cannot hold (SPEC-96). The
# contrast stays in the Holm family with its p as computed (k is fixed by the
# schedule, not by outcomes).
MIN_CLUSTERS = 7


def _key(measure: tuple) -> str:
    m, judge = measure
    return m if judge is None else f"{m} {judge}"


def floor(prs: list[tuple], measure: str, chance: float | None = None) -> dict:
    """A's accuracy on `measure` over the contrast's pairs, per cluster, with
    its t-interval; `at_floor` per the module docstring."""
    by: dict = defaultdict(list)
    for _, c in prs:
        if c[measure] is not None:
            by[(c["book"], c["position"])].append(c[measure])
    means = [mean(v) for _, v in sorted(by.items())]
    ci = t_interval(means)
    out = {
        "a_mean": round(mean(means), 4) if means else None,
        "t_ci": ci,
        "clusters": len(means),
    }
    if chance is None:  # M2: forced choice between two narrators
        out["at_floor"] = ci is None or ci[0] <= 0.5 <= ci[1]
    else:
        out["chance"] = round(chance, 4)
        out["at_floor"] = ci is None or ci[0] <= chance + CHANCE_MARGIN
    return out


def m3_chance(prs: list[tuple]) -> float | None:
    lines = sum(c.get("m3_lines") or 0 for _, c in prs)
    total = sum(c.get("m3_chance_sum") or 0 for _, c in prs)
    return total / lines if lines else None


def decision(
    contrast: str, s: dict, test_: dict, untested: str | None, floor_: dict | None
) -> str:
    judged_ = contrast in ("N", "V")
    caveat = CAVEAT.format(judged="Sonnet-judged, " if judged_ else "")
    if floor_ and floor_["at_floor"]:
        return f"{contrast}: {FLOOR} (A {floor_['a_mean']}, t-interval {floor_['t_ci']}) ({caveat})"
    if contrast == "V":
        return (
            f"V: descriptive only (pre-registration): d = {s['d']}, t-interval "
            f"{s['t_ci']}, n = {s['n_pairs']} pairs ({caveat})"
        )
    if untested:
        return (
            f"{contrast}: not tested ({untested}): descriptive only (d = {s['d']}, "
            f"n = {s['n_pairs']}) ({caveat})"
        )
    if (s.get("n_clusters") or 0) < MIN_CLUSTERS:
        return (
            f"{contrast}: undetermined: too few clusters (k = {s.get('n_clusters') or 0}"
            f" < {MIN_CLUSTERS}; d = {s['d']}, n = {s['n_pairs']}) ({caveat})"
        )
    holds = test_["reject"] and s["d"] is not None and s["d"] < 0
    yes, no = _WORDING[contrast]
    follow = _FOLLOW[contrast][0 if holds else 1]
    line = (yes if holds else no).format(d=abs(s["d"] or 0), n=s["n_pairs"])
    if not holds and test_["reject"]:
        if contrast in _REVERSED:
            line = _REVERSED[contrast].format(d=s["d"])
        else:
            line = (
                f"the difference is the other way (d = {s['d']}, Holm p = "
                f"{test_['p_holm']}): the hypothesis does not hold"
            )
        follow = None
    return f"{contrast}: {line}" + (f"; {follow}" if follow else "") + f" ({caveat})"


def per_arm(rows: list[dict]) -> dict:
    """Outcomes per arm (failure class, chapters with no scenes), M3's
    eligible lines and the 8-gram sample overlap, over all its chapters."""
    out = {}
    for a in ("A", *CONTRASTS):
        mine = [r for r in rows if r["arm"] == a]
        fail = Counter(r["failure"] or "none" for r in mine)
        ov = [r["sample_overlap"] for r in mine if r["sample_overlap"] is not None]
        out[a] = {
            "chapters": len(mine),
            "failure": dict(sorted(fail.items())),
            "no_scenes": sum(1 for r in mine if not r["scenes"]),
            "judged": sum(1 for r in mine if r["judged"]),
            "m3_eligible_lines": sum(r.get("m3_lines") or 0 for r in mine),
            "m3_narrator_lines": sum(r.get("m3_narrator_lines") or 0 for r in mine),
            "sample_overlap_8gram": {
                "chapters": len(ov),
                "mean": round(mean(ov), 5) if ov else None,
                "max": round(max(ov), 5) if ov else None,
                "with_any": sum(1 for v in ov if v > 0),
            },
        }
    return out


def analyze(
    rows: list[dict], validated: dict | None = None, resamples: int = RESAMPLES
) -> dict:
    """Every contrast's measures, the family, sensitivities and decisions."""
    validated = {"P": True, "N": True} | (validated or {})
    measures = list(M1) + [f"{m} {j}" for m in ("m2", "m3", "m3_own") for j in JUDGES]
    out: dict = {
        "label": "W21, SPEC-96; "
        + CAVEAT.format(judged="judge measures Sonnet-judged, "),
        "per_arm": per_arm(rows),
        "contrasts": {},
    }
    prs_of = {}
    for contrast in CONTRASTS:
        prs, counts = pairs(rows, contrast)
        prs_of[contrast] = prs
        res: dict = {"counts": counts}
        for m in measures:
            clusters, missing = differences(prs, m)
            res[m] = test(clusters, resamples) | {"left_out": missing}
        counts["m3_no_line"] = res["m3 sonnet"]["left_out"]
        counts["m2_not_judged"] = res["m2 sonnet"]["left_out"]
        counts["m3_eligible_lines"] = eligible_lines(prs)
        out["contrasts"][contrast] = res
    untested = {c: "its measure failed validation" for c in PRIMARY if not validated[c]}
    family = [c for c in PRIMARY if c not in untested]
    keys = {c: _key(m) for c, m in (PRIMARY | DESCRIPTIVE).items()}
    tests = holm({c: out["contrasts"][c][keys[c]]["p"] for c in family})
    none = {"p_holm": None, "reject": False}
    floors = {
        "N": floor(prs_of["N"], "m2 sonnet"),
        "V": floor(prs_of["V"], "m3 sonnet", m3_chance(prs_of["V"]) or 0.0),
    }
    out["family"] = family
    out["floors"] = floors
    out["primary"] = {
        c: {
            "measure": keys[c],
            "tested": c in family,
            "untested_because": untested.get(c)
            or ("descriptive (pre-registration)" if c in DESCRIPTIVE else None),
        }
        | {k: v for k, v in out["contrasts"][c][keys[c]].items()}
        | tests.get(c, none)
        for c in CONTRASTS
    }
    # sensitivities
    p_sent = out["contrasts"]["P"]["person_mismatch"]
    p_main = out["contrasts"]["P"][keys["P"]]
    fp = [(t, c) for t, c in prs_of["N"] if t["first_person"] and c["first_person"]]
    out["sensitivities"] = {
        "a: H1 on sentence-level person_mismatch": p_sent
        | {
            "direction_agrees": (
                (p_sent["d"] or 0) * (p_main["d"] or 0) > 0
                if p_sent["d"] and p_main["d"]
                else None
            )
        },
        "b: N vs A on person_mismatch_window": out["contrasts"]["N"][
            "person_mismatch_window"
        ],
        "c: H2 on pairs both first person": test(
            differences(fp, "m2 sonnet")[0], resamples
        )
        | {"pairs_first_person": len(fp)},
        "d: M3 without narrator-credited lines": out["contrasts"]["V"]["m3_own sonnet"],
    }
    out["decisions"] = [
        decision(
            c,
            out["contrasts"][c][keys[c]],
            tests.get(c, none),
            untested.get(c),
            floors.get(c),
        )
        for c in CONTRASTS
    ]
    return out


def render(res: dict) -> str:
    lines = [res["label"], "per arm:"]
    lines += [f"  {a}: {json.dumps(v)}" for a, v in res["per_arm"].items()]
    lines.append(
        f"  {'contrast':8s} {'measure':22s} {'d (T-A)':>8s} {'t-interval':>18s} "
        f"{'sign-flip p':>11s} {'pairs':>5s} {'clusters':>8s}  bootstrap (descriptive)"
    )
    for c, r in res["contrasts"].items():
        for m, s in r.items():
            if m == "counts":
                continue
            ci = f"[{s['t_ci'][0]:.3f}, {s['t_ci'][1]:.3f}]" if s["t_ci"] else "n/a"
            b = s["bootstrap (descriptive)"]
            mark = " *" if m == res["primary"][c]["measure"] else ""
            lines.append(
                f"  {c:8s} {m + mark:22s} {s['d']!s:>8s} {ci:>18s} {s['p']!s:>11s} "
                f"{s['n_pairs']:5d} {s['n_clusters']:8d}  ci {b['ci']} p {b['p']}"
            )
        lines.append(f"  {c:8s} counts {json.dumps(r['counts'])}")
    lines.append(f"family (Holm on the sign-flip p): {res['family']}")
    for c, p in res["primary"].items():
        lines.append(
            f"  {c}: {p['measure']} d = {p['d']} p = {p['p']} Holm p = "
            f"{p['p_holm']} reject = {p['reject']} tested = {p['tested']}"
            + (f" ({p['untested_because']})" if p["untested_because"] else "")
        )
    lines.append(f"instrument floors: {json.dumps(res['floors'])}")
    lines.append("sensitivities (descriptive):")
    for k, s in res["sensitivities"].items():
        extra = {x: s[x] for x in ("direction_agrees", "pairs_first_person") if x in s}
        lines.append(f"  {k}: d = {s['d']} p = {s['p']} t {s['t_ci']} {extra or ''}")
    lines.append("decisions:")
    lines += [f"  {d}" for d in res["decisions"]]
    return "\n".join(lines)


def _validated(m1: Path | None) -> dict:
    if not m1:
        return {}
    summary = json.loads(m1.read_text("utf-8"))["summary"]
    return {"P": bool(summary.get(f"validated {PRIMARY['P'][0]}"))}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dates")
    d.add_argument("runs", nargs="+")
    d.add_argument("--repo", required=True)
    r = sub.add_parser("run")
    r.add_argument("reports", type=Path, nargs="+")
    r.add_argument("--app-sha", required=True)
    r.add_argument("--days", type=Path, required=True)
    r.add_argument("--m1-validation", type=Path)
    r.add_argument("--resamples", type=int, default=RESAMPLES)
    r.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "dates":
        print(json.dumps(dates(a.runs, a.repo), indent=1))
        return
    days = json.loads(a.days.read_text("utf-8"))
    rows = chapters(a.reports, days, a.app_sha)
    res = analyze(rows, _validated(a.m1_validation), a.resamples)
    print(render(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
