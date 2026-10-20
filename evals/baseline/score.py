"""W12-5 (e): the statistic pre-registered in SPEC-84 (P(B>A), P(C>A), 97.5%
slot-cluster bootstrap CIs over 2,000 resamples, ties 1/2; P(C>B) descriptive)."""

import math
import random
from collections import defaultdict
from statistics import mean

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


def score(records: list[dict], judgments: list[tuple]) -> dict:
    out = {f"P({x}>A)": win_rate(judgments, x, "A") for x in "BC"}
    wins = [k for k, r in out.items() if r["ci"] and r["ci"][0] > 0.5]
    out["decision"] = f"{', '.join(wins)} excludes 0.5" if wins else "simplify toward A"
    out["P(C>B) descriptive"] = win_rate(judgments, "C", "B")
    for arm in "ABC":
        rs = [r for r in records if r["arm"] == arm]
        lat = [r["latency_ms"] for r in rs if r["latency_ms"] is not None]
        out[arm] = {
            "scenes": len(rs),
            "latency_ms_p50": _pct(lat, 0.5),
            "latency_ms_p95": _pct(lat, 0.95),
            "requests_per_scene": mean(r["requests"] for r in rs) if rs else None,
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in rs),
        }
    return out
