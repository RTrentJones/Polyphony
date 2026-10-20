"""W12-5 (c)(d): blinding and the rater sheet (SPEC-84); the seed and the
id -> (slot, arm) key are sealed outside the bundle (`step_baseline`)."""

import csv
import io
import random
import re
from collections import defaultdict

# Fences, the current-beat arrow, beat labels and [[eval-…]] tags.
MARKERS = re.compile(
    r"```\w*|→|\[\[eval-[^\]]*\]\]|^[ \t*_#]*beat \d+\s*[:.)-]?[ \t*_]*", re.M | re.I
)


def strip_markers(text: str) -> str:
    return MARKERS.sub("", text).strip()


def blind(records: list[dict], seed: int) -> tuple[dict, dict]:
    rng = random.Random(seed)
    by_slot: dict = defaultdict(dict)
    for r in records:
        by_slot[r["slot"]][r["arm"]] = r
    bundle: dict = {"items": {}, "pairs": [], "edit": []}
    key: dict = {"seed": seed, "items": {}}
    for slot, arms in sorted(by_slot.items()):
        ids = {arm: f"i{rng.getrandbits(48):012x}" for arm in sorted(arms)}
        for arm, i in ids.items():
            bundle["items"][i] = strip_markers(arms[arm]["text"])
            key["items"][i] = [slot, arm]
        for x, y in (("A", "B"), ("A", "C"), ("B", "C")):
            if x in ids and y in ids:
                bundle["pairs"].append(rng.sample([ids[x], ids[y]], 2))
        bundle["edit"].append(ids[rng.choice(sorted(ids))])
    rng.shuffle(bundle["pairs"])
    rng.shuffle(bundle["edit"])
    bundle["items"] = dict(sorted(bundle["items"].items()))  # id order, not arm order
    return bundle, key


def unblind(prefs: list, key: dict) -> list[tuple]:
    """[((id, id), winner id | None for a tie)] -> [(slot, arm, arm, winner arm)]."""
    k = key["items"]
    return [(k[a][0], k[a][1], k[b][1], won and k[won][1]) for (a, b), won in prefs]


def sheet(bundle: dict) -> str:
    """The rater sheet: one row per task; raters fill `answer` (and `minutes`)."""
    tasks = [
        ("prefer: the better next scene (item, other or tie)", a, b)
        for a, b in bundle["pairs"]
    ]
    tasks += [
        ("continuity: contradictions of the canon sheet (count)", i, "")
        for i in bundle["items"]
    ]
    tasks += [("edit to acceptable (paste the edit)", i, "") for i in bundle["edit"]]
    out = io.StringIO()
    csv.writer(out).writerows([("task", "item", "other", "answer", "minutes"), *tasks])
    return out.getvalue()
