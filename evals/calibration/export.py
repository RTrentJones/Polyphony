"""W19 S4 (SPEC-94, D19-5): the human calibration set, exported blind.

Two kinds of item, rebuilt from records the harness already wrote (no LLM call):

- **r2**: one judged scene's R2 input, as the judge saw it (pipeline.step_prose):
  each candidate's voice samples (`_r2_gold`, the corpus at the report's
  `harness_sha`) and the numbered spoken lines credited to a cast speaker who
  has samples, names masked (`mask_names`). The rater names each line's speaker.
- **pair**: one W12-9 preference pair, as the panel saw it (baseline/panel.py):
  the canon block, the two passages, the panel's question. Side order is
  re-randomised here.

Inputs are artifact directories downloaded inside CI (calibration.yml):
eval-report-* (evals.yml), baseline-bundle-* and baseline-ratings
(baseline.yml), and, only when asked, baseline-key-* for arm labels.

Outputs are two files, uploaded as two artifacts: `items.json` (what the page
shows; no speaker, verdict, score, arm, slot, run or bundle id) and `key.json`
(read only by evals.calibration.agreement, which prints aggregates).

python -m evals.calibration.export --reports R --bundles B --ratings P
    [--keys K] [--seed N] [--n-r2 20] [--n-pairs 20] [--panel-sha SHA]
    --items items.json --key key.json
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ITEMS_SCHEMA = "polyphony-calibration-items/1"
KEY_SCHEMA = "polyphony-calibration-key/1"
# baseline/panel.py ASK["preferred"], without its JSON answer format (the page
# answers A or B); a test holds it to the panel's own string.
PAIR_QUESTION = "Which passage is better?"
PANEL_PROMPT = (
    'Which passage is better? Answer with JSON {"preferred": 1} or {"preferred": 2}.'
)
R2_QUESTION = (
    "Speaker tags and names are removed ([name]). Using the voice samples, "
    "name the speaker of each numbered line: one of the candidates."
)
PREFER_TASK = "prefer: the better next scene (item, other or tie)"  # blind.PREFER
CORPUS = "evals/corpora/{book}/ground_truth.json"


def ground_truth(book: str, sha: str | None) -> tuple[dict, str]:
    """The corpus' ground truth at `sha` (what the judge read), else the
    checkout's; returns (gt, where it came from)."""
    if sha:
        try:
            out = subprocess.run(
                ["git", "show", f"{sha}:{CORPUS.format(book=book)}"],
                capture_output=True,
                check=True,
                text=True,
            )
            return json.loads(out.stdout), sha
        except (subprocess.CalledProcessError, OSError, json.JSONDecodeError):
            pass
    from evals.steps.pipeline import load_corpus

    return load_corpus(book)[1], "checkout"


# --- R2 ----------------------------------------------------------------------


def r2_candidates(reports: Path, gt_at=None) -> list[dict]:
    """Every judged scene with an R2 score, rebuilt as the judge's input.

    One scene's lines graded by several reports (a cached prose re-graded) are
    one candidate carrying every grading's score."""
    from evals.harness.rescore import null_unparseable
    from evals.steps.pipeline import _forms, _r2_gold, mask_names

    gt_at = gt_at or ground_truth
    seen: dict[tuple, dict] = {}
    for path in sorted(reports.rglob("eval-report-*.json")):
        run = json.loads(path.read_text("utf-8"))
        book = run.get("book")
        prose = (run.get("steps") or {}).get("prose") or {}
        if not book or not isinstance(prose.get("scenes"), list):
            continue
        gt, gt_from = gt_at(book, run.get("harness_sha"))
        gold, forms = _r2_gold(gt), _forms(gt)
        alias_forms = [f for fs in forms.values() for f in fs]
        for row in prose["scenes"]:
            r2 = dict((row.get("rubrics") or {}).get("R2") or {})
            # Review B3: a pre-S2 report carries an unparseable R2 as 0.0;
            # it is None (SPEC-94 D19-2), never a judge score of 0.
            null_unparseable(r2)
            if r2.get("score") is None:  # not judged, unparseable, not applicable
                continue
            voiced = [
                r for r in row.get("spoken_lines") or [] if gold.get(r["speaker"])
            ]
            speakers = list(dict.fromkeys(r["speaker"] for r in voiced))
            if len(speakers) < 2:
                continue
            names = list(dict.fromkeys([*(row.get("cast") or []), *speakers]))
            names = [n for n in names if gold.get(n)]
            lines = [mask_names(r["line"], alias_forms) for r in voiced]
            samples = {
                n: [*gold[n].get("train", []), *gold[n].get("test", [])] for n in names
            }
            ident = (book, tuple(lines), tuple(r["speaker"] for r in voiced))
            if ident in seen:
                seen[ident]["judge_r2"].append(r2["score"])
                seen[ident]["judge_explanations"].append(r2.get("explanation", ""))
                continue
            seen[ident] = {
                "book": book,
                "candidates": names,
                "samples": samples,
                "lines": lines,
                "speakers": [r["speaker"] for r in voiced],
                "r2_source": row.get("r2_source"),
                "judge_r2": [r2["score"]],
                "judge_explanations": [r2.get("explanation", "")],
                "judge_speakers": None,  # the R2 judge reports a score, not lines
                "report": str(path.relative_to(reports)),
                "scene_id": row.get("scene_id"),
                "harness_sha": run.get("harness_sha"),
                "gt_from": gt_from,
            }
    return list(seen.values())


def _round_robin(rows: list[dict], rng: random.Random, n: int) -> list[dict]:
    """`n` rows, shuffled within each book, then one book at a time."""
    by_book: dict = defaultdict(list)
    for r in rows:
        by_book[r["book"]].append(r)
    queues = []
    for book in sorted(by_book):
        q = by_book[book][:]
        rng.shuffle(q)
        queues.append(q)
    out: list[dict] = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def pick_r2(cands: list[dict], rng: random.Random, n: int) -> list[dict]:
    """Scenes whose speakers all come from speech tags first (their truth is
    the product's speech rule, not a judge's reading), then the rest."""
    tags = [c for c in cands if c["r2_source"] == "tags"]
    rest = [c for c in cands if c["r2_source"] != "tags"]
    got = _round_robin(tags, rng, n)
    return got + _round_robin(rest, rng, n - len(got))


# --- pairs -------------------------------------------------------------------


def _canon(gt: dict) -> str:
    """baseline/panel.load's canon block, byte for byte."""
    canon = f"{gt['synopsis']}\nCast: {', '.join(gt['cast'])}"
    return f"<canon>\n{canon}\n</canon>"


def _sheet(path: Path) -> dict:
    """A panel sheet's preferences: {(item, other): winner id | "tie"}."""
    with path.open(newline="") as f:
        return {
            (r["item"], r["other"]): r["answer"]
            for r in csv.DictReader(f)
            if r["task"] == PREFER_TASK
        }


def pair_candidates(
    bundles: Path,
    ratings: Path,
    keys: Path | None = None,
    sha: str | None = None,
    gt_at=None,
) -> list[dict]:
    """Every rated W12-9 pair, with the panel's (and each member's) verdict."""
    gt_at = gt_at or ground_truth
    panel = _sheet(ratings / "sheet-panel.csv")
    kappa = ratings / "kappa.json"
    members = json.loads(kappa.read_text())["members"] if kappa.exists() else []
    per_member = {}
    for m in members:
        p = ratings / ("sheet-%s.csv" % re.sub(r"\W", "_", m))
        if p.exists():
            per_member[m] = _sheet(p)
    arms: dict = {}
    for k in sorted(keys.rglob("key.json")) if keys else []:
        arms |= json.loads(k.read_text())["items"]
    out, books = [], set()
    for path in sorted(bundles.rglob("bundle.json")):
        book = path.parent.name.removeprefix("baseline-bundle-")
        if book in books:
            raise SystemExit(f"{path}: a second bundle for {book}; pass the rated runs")
        books.add(book)
        b = json.loads(path.read_text())
        gt, gt_from = gt_at(book, sha)
        for a, c in b["pairs"]:
            if (a, c) not in panel:
                raise SystemExit(f"{path}: pair {a},{c} has no panel verdict")
            out.append(
                {
                    "book": book,
                    "context": _canon(gt),
                    "texts": {a: b["items"][a], c: b["items"][c]},
                    "pair": [a, c],
                    "panel": panel[(a, c)],
                    "members": {m: s.get((a, c)) for m, s in per_member.items()},
                    "arms": {i: arms[i] for i in (a, c)} if arms else None,
                    "gt_from": gt_from,
                }
            )
    return out


def _side(verdict: str | None, sides: dict) -> str | None:
    """A winner id (or "tie") as the side the page shows it on."""
    if verdict in (None, "tie"):
        return verdict
    return next(s for s, i in sides.items() if i == verdict)


# --- export ------------------------------------------------------------------


def export(
    r2: list[dict], pairs: list[dict], seed: int, n_r2: int = 20, n_pairs: int = 20
) -> tuple[dict, dict]:
    """(items, key): the seeded selection, side order and item order."""
    rng = random.Random(seed)
    chosen_r2 = pick_r2(r2, rng, n_r2)
    chosen_pairs = _round_robin(pairs, rng, n_pairs)
    ids: set[str] = set()

    def new_id() -> str:
        while (i := f"c{rng.getrandbits(32):08x}") in ids:
            pass
        ids.add(i)
        return i

    items, key = [], {}
    for c in chosen_r2:
        i = new_id()
        items.append(
            {
                "id": i,
                "kind": "r2",
                "book": c["book"],
                "question": R2_QUESTION,
                "candidates": c["candidates"],
                "samples": c["samples"],
                "lines": [{"n": n, "text": t} for n, t in enumerate(c["lines"], 1)],
            }
        )
        shown = ("candidates", "samples", "lines")
        key[i] = {"kind": "r2"} | {k: v for k, v in c.items() if k not in shown}
    for p in chosen_pairs:
        i = new_id()
        first, second = rng.sample(p["pair"], 2)
        sides = {"A": first, "B": second}
        items.append(
            {
                "id": i,
                "kind": "pair",
                "book": p["book"],
                "question": PAIR_QUESTION,
                "context": p["context"],
                "text_a": p["texts"][first],
                "text_b": p["texts"][second],
            }
        )
        key[i] = {
            "kind": "pair",
            "book": p["book"],
            "bundle_pair": p["pair"],
            "sides": sides,
            "panel": _side(p["panel"], sides),
            "members": {m: _side(v, sides) for m, v in p["members"].items()},
            "arms": (
                {s: p["arms"][x][1] for s, x in sides.items()} if p["arms"] else None
            ),
            "slot": p["arms"][first][0] if p["arms"] else None,
            "gt_from": p["gt_from"],
        }
    rng.shuffle(items)
    out_items = {
        "schema": ITEMS_SCHEMA,
        "n": len(items),
        "counts": {
            "r2": len(chosen_r2),
            "pair": len(chosen_pairs),
        },
        "answer_format": {
            "r2": "a list of candidate names, one per line, in line order",
            "pair": '"A" or "B"',
        },
        "panel_prompt": PANEL_PROMPT,
        "items": items,
    }
    out_key = {
        "schema": KEY_SCHEMA,
        "seed": seed,
        "available": {"r2": len(r2), "pair": len(pairs)},
        "items": key,
    }
    return out_items, out_key


TOP = {"schema", "n", "counts", "answer_format", "panel_prompt", "items"}
SHOWN = {
    "r2": {"id", "kind", "book", "question", "candidates", "samples", "lines"},
    "pair": {"id", "kind", "book", "question", "context", "text_a", "text_b"},
    # W19 S7 (evals/validation/export.py): one chapter as the RC judge saw it.
    "chapter": {"id", "kind", "book", "question", "plan", "text"},
}


def leaks(items: dict, key: dict) -> list[str]:
    """Why `items` would unblind the rater: a field outside the page's
    whitelist, or any key-side id (bundle, scene, report, sha) in its text."""
    why = [f"top-level field {k!r}" for k in set(items) - TOP]
    for it in items.get("items", []):
        extra = set(it) - SHOWN.get(it.get("kind"), set())
        why += [f"{it.get('id')}: field {k!r}" for k in sorted(extra)]
    text = json.dumps(items, ensure_ascii=False)
    secret: set[str] = set()
    for k in key.get("items", {}).values():
        secret.update(k.get("bundle_pair") or [])
        secret.update(
            str(k[f]) for f in ("scene_id", "report", "harness_sha") if k.get(f)
        )
    found = [
        s
        for s in sorted(secret)
        if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", text)
    ]
    why += [f"key-side id {s!r} in items" for s in found]
    return why


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--bundles", type=Path, required=True)
    ap.add_argument("--ratings", type=Path, required=True)
    ap.add_argument("--keys", type=Path, default=None)
    ap.add_argument("--panel-sha", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--n-r2", type=int, default=20)
    ap.add_argument("--n-pairs", type=int, default=20)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    a = ap.parse_args(argv)
    seed = a.seed if a.seed is not None else random.SystemRandom().getrandbits(32)
    r2 = r2_candidates(a.reports)
    pairs = pair_candidates(a.bundles, a.ratings, a.keys, a.panel_sha)
    items, key = export(r2, pairs, seed, a.n_r2, a.n_pairs)
    if leaks(items, key):
        raise SystemExit("items.json would unblind the rater; nothing written")
    for path, doc in ((a.items, items), (a.key, key)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    # Counts only: never an item, a verdict or the seed.
    print(json.dumps({"items": items["counts"], "available": key["available"]}))


if __name__ == "__main__":
    main()
