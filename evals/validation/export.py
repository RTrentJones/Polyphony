"""W19 S7: a sample of the validation items for S4's human calibration page.

Adds a third item kind to `polyphony-calibration-items/1` (evals/calibration/
export.py on S4's branch): **chapter**, one item as the RC judge saw it, the
plan and the chapter text split from pipeline.rc_content's own output (scenes
trimmed with "[…]" where RC's were). The rater answers with an RC score on the
rubric's anchors, not intact/damaged: the judge reports a score, so a score is
what agreement can be measured on, and a pair's ordering (damaged below
intact) can still be read off the key; a binary verdict would need a threshold
on the judge's scale that nothing fixes.

The sample is `n/2` chapters, each with its intact item and one damaged
variant, damage types taken in turn so each appears; untrimmed chapters first
(a rater then reads the whole chapter). The key carries chapter, variant,
damage, location, the partner item's id and, with --reports, the judge's RC.
With --reports the item set is rebuilt from the reports' seed and refused
(as analyze refuses it) when its digest differs from theirs: the judge's RC
joins by item id, and an id survives a changed chapter text or plan.

python -m evals.validation.export [--n 8] [--seed N] [--reports R...]
    --items items.json --key key.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from evals.graders.rubrics import RUBRICS
from evals.validation.build import DAMAGES, DEFAULT_SEED, build
from evals.validation.step import judge_inputs

ITEMS_SCHEMA = "polyphony-calibration-items/1"
KEY_SCHEMA = "polyphony-calibration-key/1"
CHAPTER_QUESTION = (
    "Score the chapter against its plan on this rubric (0.0, 0.4, 0.7 or 1.0; "
    "between anchors allowed):\n\n" + RUBRICS["RC"]
)
CHAPTER_ANSWER = "a score 0.0-1.0 on the rubric's anchors"
SHOWN = {"id", "kind", "book", "question", "plan", "text"}
_SPLIT = "\nCHAPTER TO SCORE"


def split_rc(rc: str) -> tuple[str, str]:
    """(plan, text) of an rc_content input; plan + text == rc."""
    at = rc.index(_SPLIT)
    return rc[:at], rc[at:]


def sample(key: dict, rng: random.Random, n: int) -> list[tuple[str, str]]:
    """[(intact id, damaged id)] for n//2 chapters."""
    by: dict[str, dict] = {}
    for i, k in key.items():
        by.setdefault(k["chapter"], {}).setdefault(k["variant"], i)
    trimmed = {k["chapter"] for k in key.values() if k["rc_trimmed"]}
    chapters = sorted(by)
    rng.shuffle(chapters)
    chapters.sort(key=lambda c: c in trimmed)  # untrimmed first, stable
    out = []
    for t, ch in enumerate(chapters[: n // 2]):
        damage = DAMAGES[t % len(DAMAGES)]
        out.append((by[ch]["intact"], by[ch][damage]))
    return out


def judge_rc(reports: list[Path]) -> tuple[int, str, dict[str, list[float]]]:
    """(seed, items digest, {item id: RC scores}) of the reports."""
    from evals.validation.analyze import collect

    seed, digest, scores = collect(reports)
    return seed, digest, {i: s["rc"] for i, s in scores.items() if s.get("rc")}


def export(
    seed: int,
    n: int = 8,
    build_seed: int = DEFAULT_SEED,
    rc: dict | None = None,
    digest: str | None = None,
) -> tuple[dict, dict]:
    """`digest` (the reports' items_digest, given with `rc`) must be the
    rebuilt set's, or the export is refused: `rc` joins by item id only."""
    if digest is None:
        items_doc, key_doc = build(build_seed)
    else:
        from evals.validation.analyze import rebuild

        items_doc, key_doc = rebuild(build_seed, digest)
    by_id = {it["id"]: it for it in items_doc["items"]}
    key = key_doc["items"]
    rng = random.Random(seed)
    items, out_key = [], {}
    for a, b in sample(key, rng, n):
        for i, partner in ((a, b), (b, a)):
            it = by_id[i]
            plan, text = split_rc(judge_inputs(it, key[i]["rn_scene"])["RC"][0])
            new = f"c{rng.getrandbits(32):08x}"
            items.append(
                {
                    "id": new,
                    "kind": "chapter",
                    "book": it["book"],
                    "question": CHAPTER_QUESTION,
                    "plan": plan,
                    "text": text,
                }
            )
            out_key[new] = {
                "kind": "chapter",
                "validation_item": i,
                "partner": partner,
                "chapter": key[i]["chapter"],
                "variant": key[i]["variant"],
                "damage": key[i]["damage"],
                "location": key[i]["location"],
                "rc_trimmed": key[i]["rc_trimmed"],
                "judge_rc": (rc or {}).get(i),
            }
    # partners by exported id, once every id exists
    old_to_new = {k["validation_item"]: n_ for n_, k in out_key.items()}
    for k in out_key.values():
        k["partner"] = old_to_new[k["partner"]]
    rng.shuffle(items)
    return (
        {
            "schema": ITEMS_SCHEMA,
            "n": len(items),
            "counts": {"chapter": len(items)},
            "answer_format": {"chapter": CHAPTER_ANSWER},
            "items": items,
        },
        {
            "schema": KEY_SCHEMA,
            "seed": seed,
            "build_seed": build_seed,
            "items_digest": key_doc["items_digest"],
            "items": out_key,
        },
    )


def leaks(items: dict, key: dict) -> list[str]:
    """Why `items` would unblind the rater: a field outside SHOWN, or a key
    label (variant, damage, chapter id, validation item id) in its text."""
    why = []
    for it in items["items"]:
        why += [f"{it['id']}: field {f!r}" for f in sorted(set(it) - SHOWN)]
    text = json.dumps(items["items"], ensure_ascii=False)
    secret = {
        s for k in key["items"].values() for s in (k["chapter"], k["validation_item"])
    }
    secret |= {k["variant"] for k in key["items"].values()} - {"intact"}
    why += [f"key label {s!r} in items" for s in sorted(secret) if s in text]
    return why


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--build-seed", type=int, default=None)
    ap.add_argument("--reports", type=Path, nargs="*")
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    a = ap.parse_args(argv)
    seed = a.seed if a.seed is not None else random.SystemRandom().getrandbits(32)
    build_seed = DEFAULT_SEED if a.build_seed is None else a.build_seed
    rc = digest = None
    if a.reports:
        report_seed, digest, rc = judge_rc(a.reports)
        if a.build_seed is not None and a.build_seed != report_seed:
            raise SystemExit(
                f"--build-seed {a.build_seed} is not the reports' seed {report_seed}"
            )
        build_seed = report_seed
    items, key = export(seed, a.n, build_seed, rc, digest)
    if leaks(items, key):
        raise SystemExit("items.json would unblind the rater; nothing written")
    for path, doc in ((a.items, items), (a.key, key)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(json.dumps({"items": items["counts"]}))


if __name__ == "__main__":
    main()
