"""SPEC-95 E2: RN-v2, a narrator-sensitive forced choice, validated on the S7 set.

S7 found RN cannot tell two narrators of one book apart (SPEC-94, S7 result):
as built it scores period and register. RN-v2 asks the judge the narrower
question directly. For each S7 chapter (every one has a drift donor, another
narrator of the same book), the judge sees voice samples of two narrators,
the chapter's real narrator and the donor, labelled 1 and 2 in a seeded random
order, then one passage, and answers which narrator wrote it
({"narrator": 1|2}). Two items per chapter: the intact RN scene (the answer is
the real narrator) and the S7 `narrator_drift` scene, whose RN scene is the
donor's passage (the answer is the donor). `narrator_drift_in` is not used:
its scene is part one narrator, part the other, so it has no single answer.

The material is S7's, rebuilt through evals.validation.build (`variants`,
`scene_text`) and the pipeline's own helpers: the passage is
`scene_narration(scene)`, the samples are `narrator_samples` (three spread
paragraphs of pure narration, each cut by `_to_full_stop`). A sample whose
paragraph is in one of the chapter's two passages would answer the question
by quotation (two donor samples are: frankenstein's Creature, dracula's Nora
Vance), so such paragraphs are left out before the three are spread; with
none to leave out the samples are `narrator_samples`' exactly. A chapter's
two items show the same samples, but each item draws its own label order, so
whether the answer is 1 or 2 says nothing about intact or drifted.

This is a sibling of `rnrc_validation`, not part of it: a separate opt-in
step (`rn2_validation`, asked for by name, `--repeat` refused like S7's), its
own seed, items and key, so S7's RN/RC numbers and its item digest are
untouched. Items carry no narrator name and no answer; the key carries both;
the step records only the judge's choice, and `analyze` joins it to the key.

Scoring (D19-2): a reply without a readable 1 or 2 is None, counted as
`unparseable`, never a guess. Accuracy is per judge, over chapters as
clusters (a chapter's items, each averaged over its judgments first, are
averaged within the chapter), with a seeded chapter bootstrap 95% interval;
chance is 0.5. `chose_1` is the share of answers naming label 1 (a position
bias check, W12-9's panel was position-driven).

python -m evals.validation.rn2 build [--seed N] [--items F --key F] [--estimate]
python -m evals.validation.rn2 analyze REPORT_OR_DIR... [--json OUT]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from collections import defaultdict
from pathlib import Path

from evals.steps.base import StepContext, step
from evals.steps.pipeline import (
    _NARRATOR_MIN_WORDS,
    _NARRATOR_SAMPLE_WORDS,
    _NARRATOR_SAMPLES,
    _narrator_source,
    _paragraphs,
    _to_full_stop,
    narrator_samples,
    scene_narration,
)
from evals.validation.analyze import bootstrap_ci
from evals.validation.build import load_config, scene_text, variants

STEP = "rn2_validation"
DEFAULT_SEED = 20
ITEMS_SCHEMA = "polyphony-rn2-validation-items/1"
KEY_SCHEMA = "polyphony-rn2-validation-key/1"
ITEM_FIELDS = ("id", "book", "samples", "passage")
KINDS = ("intact", "drift")
_VARIANT = {"intact": "intact", "drift": "narrator_drift"}
_OUT_MAX, _OUT_TYPICAL = 1024, 20  # the reply is {"narrator": n}
_PRICED_AS = "claude-sonnet-5-5"
_SYSTEM_CHARS = len("Reply ONLY with JSON.")

QUESTION = (
    "Below are samples of two narrators of the same book, NARRATOR 1 and "
    "NARRATOR 2: their own narration, quoted speech removed. Then a PASSAGE of "
    "narration from the same book, its dialogue and the speech tags beside it "
    "removed. Which narrator wrote the PASSAGE? Judge by narrative voice only: "
    "diction, sentence rhythm, register, what the narrator notices and how "
    "they refer to themselves and to others."
)
ANSWER = 'Reply ONLY with JSON: {"narrator": 1} or {"narrator": 2}.'


# --- the item set -------------------------------------------------------------


def voice_samples(book: str, narrator: str, avoid: list[str]) -> list[str]:
    """narrator_samples(book, narrator), leaving out any paragraph whose
    narration opens a passage in `avoid` (it would answer by quotation). With
    nothing to leave out this is narrator_samples' result exactly."""
    samples, _ = narrator_samples(book, narrator)
    if not any(s[:80] in a for s in samples for a in avoid):
        return samples
    from app.parsing.character_extractor import narration_of

    text, _, quoted = _narrator_source(book, narrator)
    pure = []
    for p in _paragraphs(text):
        if quoted:
            p = p.strip('"“”').strip()
        n = " ".join(narration_of(p).split())
        words = len(n.split())
        if words >= _NARRATOR_MIN_WORDS and words >= 0.9 * len(p.split()):
            if not any(n[:80] in a for a in avoid):
                pure.append(n)
    k, m = _NARRATOR_SAMPLES, len(pure)
    if m > k:
        pure = [pure[i * (m - 1) // (k - 1)] for i in range(k)]
    return [_to_full_stop(x.split(), _NARRATOR_SAMPLE_WORDS) for x in pure]


def chapter_material(ch: dict) -> dict:
    """{passages: {kind: narration}, samples: {"real": [...], "donor": [...]},
    real, donor}: one S7 chapter's two passages and two narrators' samples."""
    by = {v["variant"]: v for v in variants(ch)}
    passages = {
        kind: scene_narration(
            scene_text(by[_VARIANT[kind]]["scenes"][by[_VARIANT[kind]]["rn_scene"]])
        )
        for kind in KINDS
    }
    avoid = list(passages.values())
    donor = ch["donor"]
    return {
        "passages": passages,
        "samples": {
            "real": voice_samples(ch["book"], ch["narrator"], avoid),
            "donor": voice_samples(donor["book"], donor["narrator"], avoid),
        },
        "real": ch["narrator"],
        "donor": donor["narrator"],
    }


def _digest(items: list[dict]) -> str:
    blob = json.dumps(items, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def build(seed: int = DEFAULT_SEED, chapters: list[dict] | None = None):
    """(items, key): two items per chapter with a donor, in a seeded order,
    each with its own seeded label order and id. Items show ITEM_FIELDS only."""
    rows = []
    for ch in chapters if chapters is not None else load_config():
        if not ch.get("donor"):
            continue
        mat = chapter_material(ch)
        rows += [(ch, mat, kind) for kind in KINDS]
    rng = random.Random(seed)
    rng.shuffle(rows)
    ids: set[str] = set()
    items, key = [], {}
    for ch, mat, kind in rows:
        while (i := f"n{rng.getrandbits(32):08x}") in ids:
            pass
        ids.add(i)
        order = ["real", "donor"]
        rng.shuffle(order)
        labels = {str(n): who for n, who in enumerate(order, 1)}
        wrote = "real" if kind == "intact" else "donor"
        items.append(
            {
                "id": i,
                "book": ch["book"],
                "samples": {n: mat["samples"][who] for n, who in labels.items()},
                "passage": mat["passages"][kind],
            }
        )
        key[i] = {
            "chapter": ch["id"],
            "kind": kind,
            "answer": order.index(wrote) + 1,
            "labels": {n: mat[who] for n, who in labels.items()},
        }
    return (
        {"schema": ITEMS_SCHEMA, "n": len(items), "items": items},
        {
            "schema": KEY_SCHEMA,
            "seed": seed,
            "items_digest": _digest(items),
            "items": key,
        },
    )


def prompt(item: dict) -> str:
    """The judge's question for one item: samples by label, then the passage."""
    parts = [QUESTION]
    for n in ("1", "2"):
        shown = "\n\n".join(
            f"{n}{chr(96 + k)}. {s}" for k, s in enumerate(item["samples"][n], 1)
        )
        parts.append(f"NARRATOR {n} SAMPLES:\n{shown}")
    parts.append(f"PASSAGE:\n{item['passage']}")
    parts.append(ANSWER)
    return "\n\n".join(parts)


def parse(reply) -> int | None:
    """1 or 2 from the judge's JSON; None when it names neither (D19-2)."""
    v = reply.get("narrator") if isinstance(reply, dict) else None
    if isinstance(v, str):
        v = v.strip()
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        return None  # a list or object answer is unparseable, never a crash
    return {1: 1, 2: 2, "1": 1, "2": 2}.get(v)


def estimate(items: list[dict]) -> dict:
    """Calls, prompt tokens and USD if every call went to the paid judge."""
    from app.llm.prices import PRICES

    tokens = sum((len(prompt(it)) + _SYSTEM_CHARS) // 4 for it in items)
    calls = len(items)
    price = PRICES[_PRICED_AS]
    return {
        "items": len(items),
        "judge_calls": calls,
        "prompt_tokens": tokens,
        "usd_if_all_paid": round(price.cost(tokens, calls * _OUT_TYPICAL), 2),
        "usd_worst": round(price.cost(tokens, calls * _OUT_MAX), 2),
        "priced_as": _PRICED_AS,
        "note": "free-tier judges (groq, openrouter) cost $0",
    }


# --- the step -----------------------------------------------------------------


def _ceiling() -> float | None:
    try:
        c = float(os.getenv("MONTHLY_COST_CEILING_USD") or 0)
    except ValueError:
        return None
    return c if c > 0 else None


@step(STEP, needs_api=True, repeatable=False)
async def step_rn2_validation(ctx: StepContext) -> dict:
    """Ask the run's judge chain each of this book's items, in the seeded
    order; record only the choice and who judged (never the key)."""
    from app.llm.errors import QuotaExhaustedError
    from evals.harness.judge import JudgeUnavailable

    seed = int(os.getenv("RN2_SEED") or DEFAULT_SEED)
    items, key = build(seed)
    mine = [it for it in items["items"] if it["book"] == ctx.book]
    if not mine:
        return {"skipped": True, "reason": f"no RN-v2 chapter from {ctx.book}"}
    est = estimate(mine)
    print(f"{STEP} {ctx.book}: estimate {est}", flush=True)
    ceiling = _ceiling()
    if ceiling is not None and est["usd_worst"] > ceiling:
        return {
            "skipped": True,
            "reason": f"worst-case ${est['usd_worst']} is over this run's "
            f"ceiling ${ceiling}",
            "estimate": est,
        }
    judged: dict[str, dict] = {}
    stopped = None
    for it in mine:
        if getattr(ctx.judge, "exhausted", False):
            stopped = "judge chain exhausted"
            break
        try:
            reply = await ctx.judge.ask_json(prompt(it))
        except (QuotaExhaustedError, JudgeUnavailable) as e:
            stopped = f"{type(e).__name__}: {e}"[:300]
            break
        choice = parse(reply)
        judged[it["id"]] = {
            "choice": choice,
            "unparseable": choice is None,
            "judge": getattr(ctx.judge, "provider_id", None),
        }
    n = sum(1 for r in judged.values() if r["choice"] is not None)
    return {
        "score": None,  # validation, not a quality score: nothing to trend
        "judge_explanation": f"{STEP}: {n}/{len(mine)} items answered, "
        f"{len(judged) - n} unparseable; aggregate with "
        "python -m evals.validation.rn2 analyze",
        "rn2": {
            "seed": seed,
            "items_digest": key["items_digest"],
            "book": ctx.book,
            "estimate": est,
            "stopped": stopped,
            "judged": judged,
        },
    }


# --- analysis -----------------------------------------------------------------


def collect(paths: list[Path]) -> tuple[int, str, list[dict]]:
    """(seed, digest, [{id, choice, judge}]) over every report's judgments."""
    files: dict[Path, None] = {}
    for p in paths:
        for f in sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]:
            files[f.resolve()] = None
    sets, rows = set(), []
    for f in files:
        res = (json.loads(f.read_text("utf-8")).get("steps") or {}).get(STEP) or {}
        v = res.get("rn2")
        if not v:
            continue
        sets.add((v["seed"], v["items_digest"]))
        for i, r in v["judged"].items():
            rows.append({"id": i, "choice": r.get("choice"), "judge": r.get("judge")})
    if len(sets) != 1:
        raise SystemExit(f"need results from exactly one item set, got {len(sets)}")
    ((seed, digest),) = sets
    return seed, digest, rows


def rebuild(seed: int, digest: str) -> dict:
    """build(seed)'s key, refused when its digest is not the reports'."""
    _, key = build(seed)
    if key["items_digest"] != digest:
        raise SystemExit(
            "the reports judged a different item set than this checkout builds"
        )
    return key


def summarize(rows: list[dict], key: dict, resamples: int = 2000, seed: int = 0):
    """Accuracy of one judge's `rows` against `key` ({id: {chapter, kind,
    answer}}): an item's judgments averaged first, then a chapter's items, so
    each chapter weighs once; unparseable judgments are counted, not scored."""
    per_item: dict[str, list[float]] = defaultdict(list)
    chose_1, unparseable = [], 0
    for r in rows:
        if r["choice"] is None:
            unparseable += 1
            continue
        per_item[r["id"]].append(float(r["choice"] == key[r["id"]]["answer"]))
        chose_1.append(r["choice"] == 1)
    by_ch: dict[str, list[float]] = defaultdict(list)
    for i, hits in per_item.items():
        by_ch[key[i]["chapter"]].append(sum(hits) / len(hits))
    chapter_acc = [sum(v) / len(v) for _, v in sorted(by_ch.items())]
    m = len(chapter_acc)
    acc = sum(chapter_acc) / m if m else None
    ci = bootstrap_ci(chapter_acc, resamples, seed)
    return {
        "judgments": len(rows),
        "unparseable": unparseable,
        "items": len(per_item),
        "n_chapters": m,
        "accuracy": None if acc is None else round(acc, 4),
        "ci95": ci,
        "chance": 0.5,
        "above_chance": bool(ci and ci[0] > 0.5),
        "chose_1": round(sum(chose_1) / len(chose_1), 4) if chose_1 else None,
    }


def analyze(paths: list[Path], resamples: int = 2000) -> dict:
    """Per judge: overall, and per kind (intact: answer the real narrator;
    drift: answer the donor)."""
    seed, digest, rows = collect(paths)
    key = rebuild(seed, digest)["items"]
    by_judge: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_judge[r["judge"] or "unknown"].append(r)
    out = {}
    for judge, rs in sorted(by_judge.items()):
        out[judge] = {"all": summarize(rs, key, resamples)}
        for kind in KINDS:
            mine = [r for r in rs if key[r["id"]]["kind"] == kind]
            out[judge][kind] = summarize(mine, key, resamples)
    return {"seed": seed, "items": len(key), "judges": out}


def render(result: dict) -> str:
    lines = [
        f"RN-v2 forced choice (seed {result['seed']}, {result['items']} items; "
        "chance 0.5; 95% CI over chapters)",
        f"  {'judge':22s} {'kind':7s} judg unp items ch  accuracy [95% CI]   chose_1",
    ]
    for judge, rows in result["judges"].items():
        for kind, s in rows.items():
            ci = s["ci95"]
            ci_s = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "[n/a]     "
            acc = "  n/a" if s["accuracy"] is None else f"{s['accuracy']:.2f}"
            c1 = "n/a" if s["chose_1"] is None else f"{s['chose_1']:.2f}"
            lines.append(
                f"  {judge:22s} {kind:7s} {s['judgments']:4d} {s['unparseable']:3d}"
                f" {s['items']:5d} {s['n_chapters']:2d}  {acc} {ci_s}  {c1}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--seed", type=int, default=DEFAULT_SEED)
    b.add_argument("--items", type=Path)
    b.add_argument("--key", type=Path)
    b.add_argument("--estimate", action="store_true", help="print calls and cost")
    a_ = sub.add_parser("analyze")
    a_.add_argument("reports", type=Path, nargs="+")
    a_.add_argument("--resamples", type=int, default=2000)
    a_.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        result = analyze(a.reports, a.resamples)
        print(render(result))
        if a.json:
            a.json.write_text(json.dumps(result, indent=1))
        return
    items, key = build(a.seed)
    for path, doc in ((a.items, items), (a.key, key)):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(json.dumps({"items": items["n"], "digest": key["items_digest"]}))
    if a.estimate:
        print(json.dumps(estimate(items["items"]), indent=1))


if __name__ == "__main__":
    main()
