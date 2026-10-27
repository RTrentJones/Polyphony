"""SPEC-95 E5: a judge answers the human calibration set, exactly as the page asks.

The items are `items.json` of `polyphony-calibration-items/1` (S4's r2 and
pair items, evals/calibration/export.py; S7's chapter items,
evals/validation/export.py). Each item is put to the run's judge chain with
the page's own question and material, and answered in the page's own shape:

- r2: a candidate name per numbered line, in line order (a list);
- pair: "A" or "B" to the panel's question;
- chapter: an RC score on the rubric's anchors (0.0 / 0.4 / 0.7 / 1.0,
  between anchors allowed).

A reply that is not such an answer (wrong length, a name outside the
candidates, neither A nor B, a score outside 0..1) is None and counted as
unparseable (SPEC-94 D19-2), never a guess. This never reads the key: it is
given items.json only and refuses a file whose schema is not the items'.

It runs as the opt-in eval step `calibration_answers` (asked for by name,
never in `all`, `--repeat` refused), so it judges on evals.yml's judge chain
(`judge=claude` puts Sonnet first) under the same spend ceiling and key
wiring, and its spend lands in the eval-report the month's budget counts.
Each book's report answers that book's items (EVAL_CALIBRATION_ITEMS names
items.json). `collect` merges the reports into the {item_id: answer} file
evals.calibration.agreement reads:

python -m evals.calibration.answer collect REPORT_OR_DIR... --out answers.json
    [--judge PROVIDER]
python -m evals.calibration.answer estimate items.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from evals.steps.base import StepContext, step

STEP = "calibration_answers"
ITEMS_SCHEMA = "polyphony-calibration-items/1"
ITEMS_ENV = "EVAL_CALIBRATION_ITEMS"
_OUT_MAX, _OUT_TYPICAL = 1024, 60
_PRICED_AS = "claude-sonnet-5-5"
_SYSTEM_CHARS = len("Reply ONLY with JSON.")


def load_items(path: Path) -> dict:
    """items.json, refused unless it is the page's items (never a key)."""
    doc = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != ITEMS_SCHEMA:
        raise ValueError(f"{path} is not {ITEMS_SCHEMA} (a key is never read)")
    return doc


def digest(doc: dict) -> str:
    blob = json.dumps(doc["items"], sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


# --- the question, per kind ----------------------------------------------------


def _r2_prompt(it: dict) -> str:
    samples = "\n\n".join(
        f"{name}:\n" + "\n".join(f"- {s}" for s in it["samples"].get(name) or [])
        for name in it["candidates"]
    )
    lines = "\n".join(f"{ln['n']}. {ln['text']}" for ln in it["lines"])
    return (
        f"{it['question']}\n\nCANDIDATES: {', '.join(it['candidates'])}\n\n"
        f"VOICE SAMPLES:\n{samples}\n\nNUMBERED LINES:\n{lines}\n\n"
        'Reply ONLY with JSON: {"speakers": [...]}, exactly '
        f"{len(it['lines'])} entries in line order, each one of the CANDIDATES."
    )


def _pair_prompt(it: dict) -> str:
    return (
        f"{it['context']}\n\nPASSAGE A:\n{it['text_a']}\n\n"
        f"PASSAGE B:\n{it['text_b']}\n\n{it['question']}\n\n"
        'Reply ONLY with JSON: {"preferred": "A"} or {"preferred": "B"}.'
    )


def _chapter_prompt(it: dict) -> str:
    return (
        f"{it['question']}\n\n{it['plan']}{it['text']}\n\n"
        'Reply ONLY with JSON: {"score": <0.0-1.0>}.'
    )


PROMPTS = {"r2": _r2_prompt, "pair": _pair_prompt, "chapter": _chapter_prompt}


def prompt(it: dict) -> str:
    return PROMPTS[it["kind"]](it)


def parse(it: dict, reply) -> object | None:
    """The page-shaped answer from the judge's JSON, or None (unparseable)."""
    if not isinstance(reply, dict):
        return None
    if it["kind"] == "r2":
        got = reply.get("speakers")
        if not isinstance(got, list) or len(got) != len(it["lines"]):
            return None
        names = {c.strip().casefold(): c for c in it["candidates"]}
        out = [
            names.get(g.strip().casefold()) if isinstance(g, str) else None for g in got
        ]
        return None if None in out else out
    if it["kind"] == "pair":
        v = reply.get("preferred")
        v = v.strip().upper() if isinstance(v, str) else v
        return {"A": "A", "B": "B", 1: "A", 2: "B", "1": "A", "2": "B"}.get(v)
    if it["kind"] == "chapter":
        v = reply.get("score")
        if isinstance(v, str):
            try:
                v = float(v.strip())
            except ValueError:
                return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if 0.0 <= v <= 1.0 else None
    return None


def estimate(items: list[dict]) -> dict:
    """Calls, prompt tokens and USD if every call went to the paid judge."""
    from app.llm.prices import PRICES

    asked = [it for it in items if it.get("kind") in PROMPTS]
    tokens = sum((len(prompt(it)) + _SYSTEM_CHARS) // 4 for it in asked)
    price = PRICES[_PRICED_AS]
    return {
        "items": len(asked),
        "kinds": dict(Counter(it["kind"] for it in asked)),
        "judge_calls": len(asked),
        "prompt_tokens": tokens,
        "usd_if_all_paid": round(price.cost(tokens, len(asked) * _OUT_TYPICAL), 2),
        "usd_worst": round(price.cost(tokens, len(asked) * _OUT_MAX), 2),
        "priced_as": _PRICED_AS,
    }


# --- the step -----------------------------------------------------------------


def _ceiling() -> float | None:
    try:
        c = float(os.getenv("MONTHLY_COST_CEILING_USD") or 0)
    except ValueError:
        return None
    return c if c > 0 else None


@step(STEP, needs_api=True, repeatable=False)
async def step_calibration_answers(ctx: StepContext) -> dict:
    from app.llm.errors import QuotaExhaustedError
    from evals.harness.judge import JudgeUnavailable

    path = os.getenv(ITEMS_ENV)
    if not path:
        return {"skipped": True, "reason": f"{ITEMS_ENV} names no items.json"}
    doc = load_items(Path(path))
    mine = [
        it
        for it in doc["items"]
        if it.get("book") == ctx.book and it["kind"] in PROMPTS
    ]
    if not mine:
        return {"skipped": True, "reason": f"no calibration item from {ctx.book}"}
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
    answers: dict[str, object] = {}
    judges: dict[str, str | None] = {}
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
        answers[it["id"]] = parse(it, reply)
        judges[it["id"]] = getattr(ctx.judge, "provider_id", None)
    unparseable = sum(1 for a in answers.values() if a is None)
    return {
        "score": None,  # answers for the agreement script, nothing to trend
        "judge_explanation": f"{STEP}: {len(answers) - unparseable}/{len(mine)} "
        f"items answered, {unparseable} unparseable; merge with "
        "python -m evals.calibration.answer collect",
        "calibration": {
            "items_digest": digest(doc),
            "book": ctx.book,
            "estimate": est,
            "stopped": stopped,
            "unparseable": unparseable,
            "answers": answers,
            "judges": judges,
        },
    }


# --- merging the reports -------------------------------------------------------


def collect(paths: list[Path], judge: str | None = None) -> tuple[dict, dict]:
    """({item_id: answer}, counts) over every report's answers: unparseable
    answers are left out (unanswered to agreement) and counted; with `judge`,
    only answers that judge gave are kept."""
    files: dict[Path, None] = {}
    for p in paths:
        for f in sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]:
            files[f.resolve()] = None
    digests, answers = set(), {}
    by_judge: Counter = Counter()
    unparseable = other_judge = 0
    for f in files:
        res = (json.loads(f.read_text("utf-8")).get("steps") or {}).get(STEP) or {}
        c = res.get("calibration")
        if not c:
            continue
        digests.add(c["items_digest"])
        for i, a in c["answers"].items():
            who = (c.get("judges") or {}).get(i)
            if judge and who != judge:
                other_judge += 1
                continue
            if a is None:
                unparseable += 1
                continue
            answers[i] = a
            by_judge[who or "unknown"] += 1
    if len(digests) != 1:
        raise SystemExit(f"need answers to exactly one items.json, got {len(digests)}")
    counts = {
        "answered": len(answers),
        "unparseable": unparseable,
        "by_judge": dict(by_judge),
    }
    if judge:
        counts["other_judge"] = other_judge
    return answers, counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("reports", type=Path, nargs="+")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--judge", help="keep only this provider's answers")
    e = sub.add_parser("estimate")
    e.add_argument("items", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "estimate":
        print(json.dumps(estimate(load_items(a.items)["items"]), indent=1))
        return
    answers, counts = collect(a.reports, a.judge)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(answers, indent=1, ensure_ascii=False))
    print(json.dumps(counts))  # counts only, never an answer


if __name__ == "__main__":
    main()
