"""The `chapters_compare` eval step (E4): two releases' chapters, judged blind.

Reads this book's `chapters` results from EVAL_PRIOR_REPORTS (evals.yml's
from_runs): exactly two runs, two different app shas, one outline digest. For
each chapter position both wrote, each judge is asked "which chapter is
better" in both orders (SPEC-84's panel rule: an answer that flips with the
order is a tie, ½), and scores each chapter on RC. Two judges, each its own
chain: Sonnet (`anthropic`, the judge's fast model) and the free chain
(EVAL_COMPARE_FREE_JUDGES, default groq,openrouter). A judge whose chain
resolves to something else (a missing key falls back to the app provider) is
not used, and the result says so.

The judge sees no release label: chapters are the scenes' text in order,
markers stripped (evals.baseline.blind), under <chapter_1>/<chapter_2>. The
report keeps outcomes by app sha; evals.chapters.analyze aggregates them.
A chapter one release failed to write (no scene text) is a forfeit: no call,
scored as a loss for that release; both empty, the pair is left out, counted.
"""

from __future__ import annotations

import os
import random
from dataclasses import replace
from types import SimpleNamespace

from evals.baseline.blind import strip_markers
from evals.chapters.generate import STEP as GENERATE
from evals.chapters.generate import prior_reports
from evals.steps.base import StepContext, step
from evals.steps.pipeline import _judge_rubrics, rc_content

STEP = "chapters_compare"
DEFAULT_SEED = 20251027
TRIES = 3  # an unparseable answer is re-asked, then left unrated (SPEC-84)
ASK = (
    "Both chapters were written for this novel from the same plan. Which "
    "chapter is better as a chapter of this novel: its prose, its fidelity to "
    "the plan and the canon, its coherence? Answer with JSON "
    '{"preferred": 1} or {"preferred": 2}.'
)


def releases(book: str) -> dict[str, dict]:
    """{app sha: that run's `chapters` result} for exactly two releases."""
    got: dict[str, dict] = {}
    for r in prior_reports(book):
        res = (r.get("steps") or {}).get(GENERATE)
        if isinstance(res, dict) and res.get("chapters"):
            sha = r.get("app_sha") or "unknown"
            if sha in got:
                raise SystemExit(f"{STEP}: two `{GENERATE}` runs of {book} at {sha}")
            got[sha] = res
    if len(got) != 2:
        raise SystemExit(
            f"{STEP}: need two releases' `{GENERATE}` runs of {book}, got {len(got)}"
        )
    if len({r["outline_digest"] for r in got.values()}) != 1:
        raise SystemExit(f"{STEP}: the two runs of {book} used different outlines")
    return got


def chapter_text(row: dict) -> str:
    return "\n\n".join(
        strip_markers(s["content"])
        for s in row.get("scenes") or []
        if (s.get("content") or "").strip()
    )


def plan_text(row: dict) -> str:
    beats = "".join(
        f"{n + 1}. {b['title']}\n"
        for n, b in enumerate(
            b for b in row.get("planned_beats") or [] if b.get("title")
        )
    )
    return (
        f"PLAN (the outline's chapter):\nCHAPTER: {row.get('title') or ''}\n"
        f"{row.get('summary') or ''}\nPLANNED BEATS (in order):\n{beats}"
    )


def prompt(canon: str, plan: str, first: str, second: str) -> str:
    fenced = [
        f"<chapter_{k}>\n{t}\n</chapter_{k}>" for k, t in ((1, first), (2, second))
    ]
    return "\n\n".join([canon, plan, *fenced, ASK])


def outcome(x: int | None, y: int | None, a: str, b: str) -> str | None:
    """Order (a, b) answered x, order (b, a) answered y: the winner's sha,
    "tie" when the answer flipped with the order, None when either is unrated."""
    if x is None or y is None:
        return None
    one, two = x == 1, y == 2
    return (a if one else b) if one == two else "tie"


async def preferred(judge, text: str) -> int | None:
    from evals.harness.judge import JudgeUnavailable

    for _ in range(TRIES):
        try:
            v = (await judge.ask_json(text)).get("preferred")
        except JudgeUnavailable:
            return None
        if type(v) is int and v in (1, 2):
            return v
    return None


def judges() -> dict:
    """{"sonnet": Judge, "free": Judge}; a chain that resolved elsewhere is None."""
    from evals.harness.config import load
    from evals.harness.judge import Judge

    cfg = load()
    free = os.getenv("EVAL_COMPARE_FREE_JUDGES", "groq,openrouter")
    want = {"sonnet": "anthropic", "free": free}
    out = {}
    for name, chain in want.items():
        j = Judge(
            replace(cfg, judge_provider=chain, judge_model=None, judge_base_url=None)
        )
        ok = not j.fell_back and j.provider_id in chain.split(",")
        out[name] = j if ok else None
    return out


@step(STEP, needs_api=True, repeatable=False)
async def step_chapters_compare(ctx: StepContext) -> dict:
    from app.llm.errors import QuotaExhaustedError

    seed = int(os.getenv("CHAPTERS_SEED") or DEFAULT_SEED)
    runs = releases(ctx.book)
    shas = sorted(runs)
    by_pos = {
        sha: {(c.get("replicate", 1), c["position"]): c for c in runs[sha]["chapters"]}
        for sha in shas
    }
    positions = sorted(set(by_pos[shas[0]]) & set(by_pos[shas[1]]))
    batch = "+".join(str(runs[sha].get("batch")) for sha in shas)
    gt = ctx.ground_truth
    canon = f"<canon>\n{gt['synopsis']}\nCast: {', '.join(gt['cast'])}\n</canon>"
    js = judges()
    pairs, stopped = [], None
    for rep, pos in positions:
        rows = {sha: by_pos[sha][(rep, pos)] for sha in shas}
        texts = {sha: chapter_text(rows[sha]) for sha in shas}
        pair: dict = {
            "position": pos,
            "replicate": rep,
            "words": {sha: len(texts[sha].split()) for sha in shas},
            "forfeit": None,
            "judges": {},
            "rc": {},
        }
        empty = [sha for sha in shas if not texts[sha]]
        if len(empty) == 2:
            pair["forfeit"] = "both"
            pairs.append(pair)
            continue
        if empty:
            pair["forfeit"] = empty[0]
            pairs.append(pair)
            continue
        a, b = random.Random(f"{seed}|{ctx.book}|{batch}|{rep}|{pos}").sample(shas, 2)
        plan = plan_text(rows[a])
        try:
            for name, j in js.items():
                if j is None or stopped or j.exhausted:
                    continue
                x = await preferred(j, prompt(canon, plan, texts[a], texts[b]))
                y = await preferred(j, prompt(canon, plan, texts[b], texts[a]))
                pair["judges"][name] = {
                    "provider": j.provider_id,
                    "orders": [[a, b, x], [b, a, y]],
                    "outcome": outcome(x, y, a, b),
                }
                if name != "sonnet":  # the free chain's quota goes to preference
                    continue
                rc = {}
                for sha in shas:
                    scenes = [
                        s["content"]
                        for s in rows[sha]["scenes"]
                        if (s.get("content") or "").strip()
                    ]
                    content, trimmed = rc_content(rows[sha], scenes)
                    got = (
                        await _judge_rubrics(SimpleNamespace(judge=j), {"RC": content})
                    )["RC"]
                    rc[sha] = {"score": got["score"], "trimmed": trimmed}
                pair["rc"][name] = rc
        except QuotaExhaustedError as e:  # the spend ceiling, or a spent cap
            stopped = f"{type(e).__name__}: {e}"[:300]
        pairs.append(pair)
    return {
        "score": None,  # aggregate with evals.chapters.analyze
        "judge_explanation": f"{STEP}: {len(pairs)} chapter pairs"
        + (f"; stopped: {stopped}" if stopped else ""),
        "compare": {
            "book": ctx.book,
            "seed": seed,
            "releases": shas,
            "batch": batch,
            "outline_digest": runs[shas[0]]["outline_digest"],
            "judges": {n: (j.provider_id if j else None) for n, j in js.items()},
            "stopped": stopped,
            "pairs": pairs,
        },
    }
