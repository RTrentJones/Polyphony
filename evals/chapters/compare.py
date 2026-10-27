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
Each pair is classed before any call (generate.failure): a chapter only one
release attempted is `not_attempted`, and a quota or provider-availability
failure on either side (`quota`) leaves the pair out on both sides, counted
per release; a product failure (a job failed otherwise, or no text) is a
forfeit, a loss for that release; both, the pair is left out, counted.

A refused or unparseable answer leaves the pair unrated, never stopping the
step. A resume passes the batch's last compare report in from_runs: its rated
pairs are copied verbatim (`resumed`), only unrated ones are judged, and the
new report names it in `resumed_from`.
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


async def preferred(judge, text: str) -> dict:
    """{"answer": 1 | 2 | None, "status", "provider"} for one order. A reply
    the provider refused or truncated (UnusableReplyError) is `refused`; three
    unparseable replies `unparseable`; a chain that gave out `unavailable`.
    None of them stops the step: the pair stays unrated, for a resume."""
    from app.llm.errors import UnusableReplyError
    from evals.harness.judge import JudgeUnavailable

    status = "unparseable"
    for _ in range(TRIES):
        try:
            v = (await judge.ask_json(text)).get("preferred")
        except JudgeUnavailable:
            status = "unavailable"
            break
        except UnusableReplyError:
            status = "refused"
            continue
        if type(v) is int and v in (1, 2):
            return {"answer": v, "status": "ok", "provider": judge.provider_id}
    return {"answer": None, "status": status, "provider": judge.provider_id}


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


def prior_compare(book: str, batch: str) -> dict | None:
    """The batch's earlier compare report from from_runs, which a resume
    continues (exactly one: pass the last of the batch)."""
    got = []
    for r in prior_reports(book):
        c = ((r.get("steps") or {}).get(STEP) or {}).get("compare")
        if isinstance(c, dict) and c.get("batch") == batch:
            got.append(c)
    if len(got) > 1:
        raise SystemExit(
            f"{STEP}: {len(got)} compare reports of batch {batch}; pass the last"
        )
    return got[0] if got else None


def _key(p: dict) -> tuple:
    return (p.get("replicate", 1), p["position"])


def _done(pair: dict, name: str) -> bool:
    return (pair.get("judges") or {}).get(name, {}).get("outcome") is not None


async def _rc(j, rows: dict) -> dict:
    """Sonnet's RC per chapter. A refused or truncated reply leaves that
    chapter's RC unscored (`refused`) and the step carries on; RC is
    descriptive and is not resumed (a resume re-asks only unrated preference
    judgments, and copies a pair's RC as it was)."""
    from app.llm.errors import UnusableReplyError

    out = {}
    for sha, row in rows.items():
        scenes = [
            strip_markers(s["content"])
            for s in row.get("scenes") or []
            if (s.get("content") or "").strip()
        ]
        content, trimmed = rc_content(row, scenes)
        try:
            got = (await _judge_rubrics(SimpleNamespace(judge=j), {"RC": content}))[
                "RC"
            ]
        except UnusableReplyError:
            out[sha] = {"score": None, "trimmed": trimmed, "status": "refused"}
            continue
        out[sha] = {"score": got["score"], "trimmed": trimmed}
    return out


def _exclusion(rows: dict, shas: list) -> tuple[str | None, object]:
    """(excluded reason, forfeit) for a pair: not attempted or a quota failure
    on either side excludes it on both; a product failure is a forfeit."""
    missing = [sha for sha in shas if sha not in rows]
    if missing:
        return "not_attempted", missing
    fail = {sha: rows[sha].get("failure") for sha in shas}
    for sha in shas:  # reports without the field: no text is the product's
        if fail[sha] is None and not chapter_text(rows[sha]):
            fail[sha] = "product"
    quota = [sha for sha in shas if fail[sha] == "quota"]
    if quota:
        return "quota", quota
    product = [sha for sha in shas if fail[sha] == "product"]
    if len(product) == 2:
        return None, "both"
    return None, (product[0] if product else None)


@step(STEP, needs_api=True, repeatable=False)
async def step_chapters_compare(ctx: StepContext) -> dict:
    import uuid

    from app.llm.errors import QuotaExhaustedError

    seed = int(os.getenv("CHAPTERS_SEED") or DEFAULT_SEED)
    runs = releases(ctx.book)
    shas = sorted(runs)
    by_key = {sha: {_key(c): c for c in runs[sha]["chapters"]} for sha in shas}
    keys = sorted(set(by_key[shas[0]]) | set(by_key[shas[1]]))
    batch = "+".join(str(runs[sha].get("batch")) for sha in shas)
    prior = prior_compare(ctx.book, batch)
    before = {_key(p): p for p in (prior or {}).get("pairs") or []}
    gt = ctx.ground_truth
    canon = f"<canon>\n{gt['synopsis']}\nCast: {', '.join(gt['cast'])}\n</canon>"
    js = judges()
    pairs, stopped = [], None
    for rep, pos in keys:
        rows = {
            sha: by_key[sha][(rep, pos)] for sha in shas if (rep, pos) in by_key[sha]
        }
        old = before.get((rep, pos))
        if old and all(_done(old, n) for n, j in js.items() if j is not None):
            pairs.append(old | {"resumed": True})  # verbatim
            continue
        texts = {sha: chapter_text(row) for sha, row in rows.items()}
        excluded, forfeit = _exclusion(rows, shas)
        pair: dict = {
            "position": pos,
            "replicate": rep,
            "words": {sha: len(t.split()) for sha, t in texts.items()},
            "failure": {sha: rows[sha].get("failure") for sha in rows},
            "excluded": excluded,
            "forfeit": None if excluded else forfeit,
            "judges": dict((old or {}).get("judges") or {}),
            "rc": dict((old or {}).get("rc") or {}),
        }
        if excluded == "not_attempted":
            pair["not_attempted"] = forfeit
        elif excluded == "quota":
            pair["quota"] = forfeit
        if excluded or pair["forfeit"]:
            pairs.append(pair)
            continue
        a, b = random.Random(f"{seed}|{ctx.book}|{batch}|{rep}|{pos}").sample(shas, 2)
        plan = plan_text(rows[a])
        if plan != plan_text(rows[b]):
            raise SystemExit(f"{STEP}: the releases' plans differ at {(rep, pos)}")
        try:
            for name, j in js.items():
                if j is None or stopped or j.exhausted or _done(pair, name):
                    continue
                x = await preferred(j, prompt(canon, plan, texts[a], texts[b]))
                y = await preferred(j, prompt(canon, plan, texts[b], texts[a]))
                pair["judges"][name] = {
                    "provider": j.provider_id,
                    "orders": [
                        [a, b, x["answer"], x["status"], x["provider"]],
                        [b, a, y["answer"], y["status"], y["provider"]],
                    ],
                    "outcome": outcome(x["answer"], y["answer"], a, b),
                }
                if name == "sonnet":  # the free chain's quota goes to preference
                    pair["rc"]["sonnet"] = await _rc(j, rows)
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
            "report_id": uuid.uuid4().hex[:12],
            "resumed_from": (prior or {}).get("report_id"),
            "outline_digest": runs[shas[0]]["outline_digest"],
            "judges": {n: (j.provider_id if j else None) for n, j in js.items()},
            "stopped": stopped,
            "counts": {
                "pairs": len(pairs),
                "unrated_sonnet": sum(
                    1
                    for p in pairs
                    if not p.get("excluded")
                    and not p.get("forfeit")
                    and not _done(p, "sonnet")
                ),
                "excluded": sum(1 for p in pairs if p.get("excluded")),
                "forfeits": sum(1 for p in pairs if p.get("forfeit")),
            },
            "pairs": pairs,
        },
    }
