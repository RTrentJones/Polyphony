"""SPEC-98 P2: the blind preference set a reader rates in about half an hour.

Each item is two passages that open the same planned scene of the same frozen
outline, written by two arms, with the chapter's summary and the scene's beat
title above them. The reader picks the better writing for that scene, or no
preference; knowing the source book does not help (the corpora are renamed).
The set is built from chapters the harness already wrote (no LLM call):

- `N`, `V`, `P`: a wave 21 treatment against wave 21's A on the same ledger
  day, book, position and replicate (SPEC-96's pairing);
- `C`: a W21-C Claude-written A against a wave 21 Flash-Lite A at the same
  book and position, on the same outline (SPEC-97), the pairs most likely to
  differ, so a judge's ranking has something to find.

Two items repeat earlier ones with the sides swapped and are shown later, so
the reader's own consistency is measured beside the judges'.

A passage is the scene's leading whole paragraphs, stopping at the first
paragraph boundary at or past MIN_WORDS; a scene whose passage would exceed
MAX_WORDS is not used (nothing is cut mid-paragraph). Both sides use the same
scene of the chapter, drawn by the seed.

Outputs: `items.json` (what the page shows: id, context, passages A and B; no
arm, run, writer or side) and `key.json` (per item: kind, the arm on each
side, runs, book, position, scene, and the item it repeats).

python -m evals.calibration.prefs --w21 DIR --w21-days F --w21c DIR
    --w21c-days F [--seed N] --items items.json --key key.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from evals.baseline.blind import strip_markers

ITEMS_SCHEMA = "polyphony-preference-items/1"
KEY_SCHEMA = "polyphony-preference-key/1"
SEED = 2026_11_19
MIN_WORDS, MAX_WORDS = 150, 300
# 16 distinct pairs and 2 swapped repeats: about 30 minutes at 2 minutes a pair.
PLAN = {"N": 5, "V": 5, "P": 3, "C": 3}
REPEATS = 2
QUESTION = (
    "Both passages open the same planned scene. Which is the better writing "
    "for it: clearer, more vivid, and truer to the plan? Pick A, B, or no "
    "preference."
)
ARMS = {None: "A", "": "A", "pov_fix": "P", "no_excerpts": "N", "no_voices": "V"}


def _rows(root: Path, days: dict, writer: str) -> list[dict]:
    """Every written chapter of the runs named in `days`, its scenes stripped
    of the harness's markers (as the judges see them) so neither side shows
    one."""
    out = []
    for path in sorted(root.glob("*/*/eval-report-*.json")):
        run = path.parent.parent.name
        report = json.loads(path.read_text("utf-8"))
        chapters = (report.get("steps") or {}).get("chapters")
        if run not in days or not chapters:
            continue
        if report.get("ablation") not in ARMS or "book" not in report:
            raise SystemExit(
                f"{path}: no book, or an unknown ablation {report.get('ablation')!r}"
            )
        for row in chapters.get("chapters") or []:
            scenes = [
                strip_markers(s.get("content") or "") for s in row.get("scenes") or []
            ]
            if row.get("failure") or not any(scenes):
                continue
            if "position" not in row:
                raise SystemExit(f"{path}: a chapter row has no position")
            out.append(
                {
                    "run": run,
                    "day": days[run],
                    "writer": writer,
                    "arm": ARMS[report.get("ablation")],
                    "book": report["book"],
                    "digest": chapters.get("outline_digest"),
                    "position": row["position"],
                    "replicate": row.get("replicate", 1),
                    "summary": row.get("summary") or "",
                    "beats": [
                        b.get("title") or "" for b in row.get("planned_beats") or []
                    ],
                    "scenes": scenes,
                }
            )
    return out


def passage(text: str) -> str | None:
    """The leading whole paragraphs of a scene, at least MIN_WORDS; None when
    that takes more than MAX_WORDS."""
    taken, words = [], 0
    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        taken.append(para)
        words += len(para.split())
        if words >= MIN_WORDS:
            break
    if words < MIN_WORDS or words > MAX_WORDS:
        return None
    return "\n\n".join(taken)


def candidates(w21: list[dict], w21c: list[dict]) -> dict[str, list[tuple]]:
    """{kind: [(left row, right row)]}: each treatment with its day's A, and
    each Claude A with every Flash-Lite A on the same outline position."""
    a = {
        (r["book"], r["day"], r["position"], r["replicate"]): r
        for r in w21
        if r["arm"] == "A"
    }
    out: dict[str, list[tuple]] = defaultdict(list)
    for t in w21:
        c = a.get((t["book"], t["day"], t["position"], t["replicate"]))
        if t["arm"] != "A" and c is not None and t["digest"] == c["digest"]:
            out[t["arm"]].append((t, c))
    flash = defaultdict(list)
    for r in w21:
        if r["arm"] == "A":
            flash[(r["book"], r["digest"], r["position"])].append(r)
    for r in w21c:
        if r["arm"] == "A":
            for f in flash[(r["book"], r["digest"], r["position"])]:
                out["C"].append((r, f))
    return out


def _draw(pairs: list[tuple], n: int, rng: random.Random, used: set) -> list[tuple]:
    """n pairs over distinct (book, position) clusters, books in turn, each
    with a shared scene whose two passages both qualify."""
    by_book = defaultdict(list)
    for p in pairs:
        by_book[p[0]["book"]].append(p)
    for v in by_book.values():
        rng.shuffle(v)
    books = sorted(by_book)
    rng.shuffle(books)
    picked = []
    while len(picked) < n and any(by_book.values()):
        for book in books:
            while by_book[book] and len(picked) < n:
                left, right = by_book[book].pop()
                cluster = (book, left["position"])
                if cluster in used:
                    continue
                shared = min(len(left["scenes"]), len(right["scenes"]))
                order = list(range(shared))
                rng.shuffle(order)
                for s in order:
                    pa, pb = passage(left["scenes"][s]), passage(right["scenes"][s])
                    if pa and pb:
                        used.add(cluster)
                        picked.append((left, right, s, pa, pb))
                        break
                else:
                    continue
                break
    if len(picked) < n:
        raise SystemExit(f"only {len(picked)} usable pairs for a draw of {n}")
    return picked


def build(w21: list[dict], w21c: list[dict], seed: int = SEED) -> tuple[dict, dict]:
    rng = random.Random(seed)
    pool = candidates(w21, w21c)
    used: set = set()
    drawn = [
        (kind, p) for kind, n in PLAN.items() for p in _draw(pool[kind], n, rng, used)
    ]
    rng.shuffle(drawn)
    items, key = [], {}
    for i, (kind, (left, right, s, pl, pr)) in enumerate(drawn, 1):
        swap = rng.random() < 0.5
        sides = [(left, pl), (right, pr)][:: -1 if swap else 1]
        item_id = f"p{i:02d}"
        beat = left["beats"][s] if s < len(left["beats"]) else ""
        items.append(
            {
                "id": item_id,
                "context": {"chapter": left["summary"], "scene": beat},
                "a": sides[0][1],
                "b": sides[1][1],
            }
        )
        key[item_id] = _key(kind, sides, s, repeat_of=None)
    for j, src in enumerate(rng.sample(items[: len(items) // 2], REPEATS), 1):
        item_id = f"p{len(drawn) + j:02d}"
        items.append({**src, "id": item_id, "a": src["b"], "b": src["a"]})
        k = key[src["id"]]
        key[item_id] = {**k, "a": k["b"], "b": k["a"], "repeat_of": src["id"]}
    return (
        {"schema": ITEMS_SCHEMA, "question": QUESTION, "items": items},
        {"schema": KEY_SCHEMA, "seed": seed, "plan": PLAN, "key": key},
    )


def _key(kind: str, sides: list, scene: int, repeat_of) -> dict:
    (a, _), (b, _) = sides

    def side(r):
        return {
            "arm": r["arm"],
            "writer": r["writer"],
            "run": r["run"],
            "day": r["day"],
        }

    return {
        "kind": kind,
        "book": a["book"],
        "position": a["position"],
        "scene": scene,
        "a": side(a),
        "b": side(b),
        "repeat_of": repeat_of,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="evals.calibration.prefs")
    ap.add_argument("--w21", type=Path, required=True)
    ap.add_argument("--w21-days", type=Path, required=True)
    ap.add_argument("--w21c", type=Path, required=True)
    ap.add_argument("--w21c-days", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    a = ap.parse_args(argv)
    w21 = _rows(a.w21, json.loads(a.w21_days.read_text("utf-8")), "flash-lite")
    w21c = _rows(a.w21c, json.loads(a.w21c_days.read_text("utf-8")), "claude")
    items, key = build(w21, w21c, a.seed)
    a.items.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    a.key.write_text(json.dumps(key, indent=1))
    words = [len((it["a"] + " " + it["b"]).split()) for it in items["items"]]
    print(
        f"{len(words)} items, {sum(words)} words to read ({min(words)}-{max(words)} a pair)"
    )


if __name__ == "__main__":
    main()
