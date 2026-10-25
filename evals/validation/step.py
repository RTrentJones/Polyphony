"""The `rnrc_validation` eval step (W19 S7): RN and RC on the validation items.

Opt-in (never part of `--steps all`) and dispatched through evals.yml like any
step, so it runs on the run's judge chain (EVAL_JUDGE_PROVIDER, Claude last
with claude=yes) and under its spend ceiling: the judge's LLMClient admits each
call against MONTHLY_COST_CEILING_USD (app/llm/spend.py) and records it in the
run's database, whose total the report's `usage` carries to evals/spend.py.
`needs_api` is set for that `usage` read; the step itself drives no app route.

Each item is judged exactly as step_prose judges a generated chapter: RC on
pipeline.rc_content(plan, scenes), RN on pipeline.rn_content(narrator_samples,
scene_narration(scene)) for one scene, both through pipeline._judge_rubrics.
A run judges the items of its `--book` (one book per report), in the items'
seeded order. The report holds scores by item id, never a label; join them to
the key with evals.validation.analyze.
"""

from __future__ import annotations

import os

from evals.graders import rubrics
from evals.steps.base import StepContext, step
from evals.steps.pipeline import (
    _judge_rubrics,
    load_style,
    narrator_samples,
    rc_content,
    rn_content,
    scene_narration,
)
from evals.validation.build import DEFAULT_SEED, build

STEP = "rnrc_validation"
# Worst case per call: the judge's max_tokens (harness/judge.py); typical
# replies are one sentence of JSON.
_OUT_MAX, _OUT_TYPICAL = 1024, 200
_PRICED_AS = "claude-sonnet-5-5"  # the paid judge of last resort (SPEC-90 D14)


def plan_chapter(plan: dict) -> dict:
    """The plan as the outline chapter rc_content reads."""
    return {
        "title": plan["title"],
        "summary": plan["summary"],
        "planned_beats": [{"title": b} for b in plan.get("beats") or []],
    }


def judge_inputs(item: dict, rn_scene: int) -> dict:
    """{"RC": (text, trimmed), "RN": text | None}: the judge's inputs, built by
    the same functions step_prose uses."""
    rc, cut = rc_content(plan_chapter(item["plan"]), item["scenes"])
    samples, _ = narrator_samples(item["book"], item["narrator"])
    narration = scene_narration(item["scenes"][rn_scene])
    rn = (
        rn_content(samples, narration, load_style(item["book"]))
        if samples and narration
        else None
    )
    return {"RC": (rc, cut), "RN": rn}


def _prompt_tokens(rid: str, content: str) -> int:
    """About what the judge is sent (harness/judge.Judge.score), ~4 chars/token."""
    return (len(rubrics.rubric(rid)) + len(content) + 400) // 4


def estimate(items: list[dict], key: dict) -> dict:
    """Calls, prompt tokens and USD if every call went to the paid judge."""
    from app.llm.prices import PRICES

    calls, tokens = 0, 0
    for it in items:
        inputs = judge_inputs(it, key["items"][it["id"]]["rn_scene"])
        tokens += _prompt_tokens("RC", inputs["RC"][0])
        calls += 1
        if inputs["RN"]:
            tokens += _prompt_tokens("RN", inputs["RN"])
            calls += 1
    price = PRICES[_PRICED_AS]
    return {
        "items": len(items),
        "judge_calls": calls,
        "prompt_tokens": tokens,
        "usd_if_all_paid": round(price.cost(tokens, calls * _OUT_TYPICAL), 2),
        "usd_worst": round(price.cost(tokens, calls * _OUT_MAX), 2),
        "priced_as": _PRICED_AS,
        "note": "free-tier judges (groq, openrouter) cost $0; a claim-empty "
        "re-ask (W15-2) adds a call",
    }


def _ceiling() -> float | None:
    try:
        c = float(os.getenv("MONTHLY_COST_CEILING_USD") or 0)
    except ValueError:
        return None
    return c if c > 0 else None


@step(STEP, needs_api=True)
async def step_rnrc_validation(ctx: StepContext) -> dict:
    from app.llm.errors import QuotaExhaustedError

    seed = int(os.getenv("RNRC_SEED") or DEFAULT_SEED)
    items, key = build(seed)
    mine = [it for it in items["items"] if it["book"] == ctx.book]
    if not mine:
        return {"skipped": True, "reason": f"no validation chapter from {ctx.book}"}
    est = estimate(mine, key)
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
        inputs = judge_inputs(it, key["items"][it["id"]]["rn_scene"])
        contents = {"RC": inputs["RC"][0]}
        if inputs["RN"]:
            contents["RN"] = inputs["RN"]
        row: dict = {"rc_trimmed": inputs["RC"][1]}
        try:
            for rid, content in contents.items():
                j = (await _judge_rubrics(ctx, {rid: content}))[rid]
                row[rid.lower()] = j["score"]
                row[f"{rid.lower()}_why"] = (j.get("explanation") or "")[:400]
                row[f"{rid.lower()}_judge"] = getattr(ctx.judge, "provider_id", None)
        except QuotaExhaustedError as e:  # the spend ceiling, or a spent cap
            stopped = f"{type(e).__name__}: {e}"[:300]
            if row.get("rc") is not None:
                judged[it["id"]] = row
            break
        judged[it["id"]] = row
    n_rc = sum(1 for r in judged.values() if r.get("rc") is not None)
    n_rn = sum(1 for r in judged.values() if r.get("rn") is not None)
    return {
        "score": None,  # validation, not a quality score: nothing to trend
        "judge_explanation": f"{STEP}: {n_rc}/{len(mine)} items RC-judged, "
        f"{n_rn} RN-judged; aggregate with evals.validation.analyze",
        "validation": {
            "seed": seed,
            "items_digest": key["items_digest"],
            "book": ctx.book,
            "estimate": est,
            "stopped": stopped,
            "judged": judged,
        },
    }
