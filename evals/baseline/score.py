"""W12-5 (e), pre-registered in SPEC-84: P(B>A) and P(C>A) as pairwise win rates,
each with a 97.5% (Bonferroni) slot-cluster bootstrap CI over 2,000 resamples,
ties counted 1/2, n = 18 slots. P(C>B) is descriptive. No Bradley-Terry.
"""

import math
import random
from collections import defaultdict
from statistics import mean

from evals.ops_scorecard import edit_distance

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


def score(
    records: list[dict], judgments: list[tuple], rated: dict | None = None
) -> dict:
    """`rated`: {(slot, arm): {"continuity": n, "edited": text, "minutes": m}}."""
    out = {f"P({x}>A)": win_rate(judgments, x, "A") for x in "BC"}
    wins = [k for k, r in out.items() if r["ci"] and r["ci"][0] > 0.5]
    out["decision"] = f"{', '.join(wins)} excludes 0.5" if wins else "simplify toward A"
    out["P(C>B) descriptive"] = win_rate(judgments, "C", "B")
    for arm in "ABC":
        rs = [r for r in records if r["arm"] == arm]
        lat = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        mine = [(r, (rated or {}).get((r["slot"], arm)) or {}) for r in rs]
        cont = [m["continuity"] for _r, m in mine if "continuity" in m]
        edits = [
            (edit_distance(r["text"], m["edited"]), m["minutes"])
            for r, m in mine
            if "edited" in m
        ]
        out[arm] = {
            "scenes": len(rs),
            "latency_ms_p50": _pct(lat, 0.5),
            "latency_ms_p95": _pct(lat, 0.95),
            "cost_per_scene": (
                sum(r["cost_usd"] or 0 for r in rs) / len(rs) if rs else None
            ),
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in rs),
            "continuity_per_scene": mean(cont) if cont else None,
            "edit_distance": mean(e for e, _m in edits) if edits else None,
            "edit_minutes": mean(m for _e, m in edits) if edits else None,
        }
    return out
