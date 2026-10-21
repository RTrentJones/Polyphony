"""W12-5 (e): the statistic pre-registered in SPEC-84 (P(B>A), P(C>A), 97.5%
slot-cluster bootstrap CIs over 2,000 resamples, ties 1/2; P(C>B) descriptive)."""

import csv
import json
import math
import sys
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from evals.baseline import blind

RESAMPLES, LEVEL = 2000, 0.975


def win_rate(judgments: list[tuple], x: str, y: str, seed: int = 0) -> dict:
    per_slot: dict = defaultdict(list)
    for slot, a, b, won in judgments:
        if {a, b} == {x, y}:
            per_slot[slot].append(0.5 if won is None else float(won == x))
    slots = sorted(per_slot)
    if not slots:
        return {"p": None, "ci": None, "slots": 0}
    rate = lambda ss: mean(v for s in ss for v in per_slot[s])  # noqa: E731
    rng = random.Random(seed)
    boot = sorted(rate(rng.choices(slots, k=len(slots))) for _ in range(RESAMPLES))
    lo = int(RESAMPLES * (1 - LEVEL) / 2)
    return {"p": rate(slots), "ci": [boot[lo], boot[-lo - 1]], "slots": len(slots)}


def _pct(v: list, q: float):
    return sorted(v)[max(0, math.ceil(q * len(v)) - 1)] if v else None


def rated(sheet: Path, key: dict) -> tuple[list, dict]:
    """A rater sheet, human or panel: (judgments, {(slot, arm): continuity})."""
    prefs, cont = [], {}
    for r in csv.DictReader(sheet.open()):
        won = None if r["answer"] == "tie" else r["answer"]
        if r["task"] == blind.PREFER:
            prefs.append(((r["item"], r["other"]), won))
        elif r["task"] == blind.CONTINUITY:
            cont[tuple(key["items"][r["item"]])] = float(r["answer"])
    return blind.unblind(prefs, key), cont


def score(records: list[dict], judgments: list, continuity=None, kappa=None) -> dict:
    out = {f"P({x}>A)": win_rate(judgments, x, "A") for x in "BC"}
    wins = [k for k, r in out.items() if r["ci"] and r["ci"][0] > 0.5]
    out["decision"] = f"{', '.join(wins)} excludes 0.5" if wins else "simplify toward A"
    if kappa is not None and kappa < 0.2:
        out["decision"] = "no decision (panel κ < 0.2)"
    out["P(C>B) descriptive"] = win_rate(judgments, "C", "B")
    cont: dict = defaultdict(list)
    for (_slot, arm), n in (continuity or {}).items():
        cont[arm].append(n)
    for arm in "ABC":
        rs = [r for r in records if r["arm"] == arm]
        lat = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        out[arm] = {
            "scenes": len(rs),
            "latency_ms_p50": _pct(lat, 0.5),
            "latency_ms_p95": _pct(lat, 0.95),
            "requests_per_scene": mean(r["requests"] for r in rs) if rs else None,
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in rs),
            "continuity_per_scene": mean(c) if (c := cont.get(arm)) else None,
        }
    return out


if __name__ == "__main__":  # <ratings dir> <key.json and records.json files>
    ratings, *files = map(Path, sys.argv[1:])
    got = [json.loads(p.read_text()) for p in files]
    key = {"items": {i: v for k in got if "items" in k for i, v in k["items"].items()}}
    records = [r for rs in got if isinstance(rs, list) for r in rs]
    if (kappa := json.loads((ratings / "kappa.json").read_text()))["unrated"]:
        raise SystemExit("panel incomplete: re-run the rate dispatch")
    judgments, cont = rated(ratings / "sheet-panel.csv", key)
    print(json.dumps(score(records, judgments, cont, kappa["fleiss_kappa"]), indent=1))
