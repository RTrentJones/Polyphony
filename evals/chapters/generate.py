"""The `chapters` eval step (E4): chapters 1..K of one book, through the product.

The same path as step_prose (a book with the corpus cast and style, the
excerpt indexed for voice, an outline, promote, generate the planned scenes),
for the first K outline chapters in order, so each release writes its own
continuous book. Nothing is judged here; chapters_compare judges two runs.

The outline is held fixed across releases: a run whose EVAL_PRIOR_REPORTS
holds this book's `chapters` result stores that run's outline (portable: cast
as names) instead of generating one; otherwise it generates one and stores its
portable form back, so both releases promote the identical outline and so get
the identical chapters and planned beats (promotion is deterministic). The
outline's digest is recorded; compare refuses a pair whose digests differ.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from evals.steps.base import StepContext, step
from evals.steps.pipeline import (
    _book_with_cast,
    _collect_scene,
    _generate_outline,
    _index_voice,
    eval_title,
)

STEP = "chapters"
DEFAULT_CHAPTERS = 3


def digest(outline: list) -> str:
    return hashlib.sha256(
        json.dumps(outline, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def prior_reports(book: str) -> list[dict]:
    """This book's reports under EVAL_PRIOR_REPORTS (evals.yml's from_runs)."""
    root = os.getenv("EVAL_PRIOR_REPORTS")
    if not root or not Path(root).is_dir():
        return []
    out = []
    for f in sorted(Path(root).rglob("eval-report-*.json")):
        try:
            r = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and r.get("book") == book:
            out.append(r | {"_file": str(f)})
    return out


def prior_outline(book: str) -> tuple[list, str] | None:
    """(portable outline, its source) from a prior `chapters` run of `book`;
    None when there is none. Two different outlines are refused."""
    found = {}
    for r in prior_reports(book):
        got = ((r.get("steps") or {}).get(STEP) or {}).get("outline")
        if isinstance(got, list) and got:
            found.setdefault(digest(got), (got, r["_file"]))
    if len(found) > 1:
        raise SystemExit(f"{STEP}: prior reports carry {len(found)} outlines")
    return next(iter(found.values()), None)


def chapter_count() -> int:
    n = int(os.getenv("EVAL_CHAPTERS") or DEFAULT_CHAPTERS)
    if not 1 <= n <= 12:
        raise SystemExit(f"EVAL_CHAPTERS must be 1..12, got {n}")
    return n


async def _write_chapter(ctx: StepContext, ch: dict, names: dict) -> dict:
    from evals.harness.client import EvalClientError

    detail = await ctx.client.get_chapter(ch["chapter_id"])
    row: dict = {
        "position": ch["position"] + 1,
        "title": detail.get("title"),
        "summary": detail.get("summary"),
        "pov": names.get(detail.get("pov_character_id") or ""),
        "planned_beats": [
            {
                "title": b.get("title"),
                "characters": [
                    names[c] for c in b.get("character_ids") or [] if c in names
                ],
            }
            for b in detail.get("planned_beats") or []
            if isinstance(b, dict)
        ],
    }
    try:
        started = await ctx.client.generate_planned(ch["chapter_id"], {})
    except EvalClientError as e:  # recorded; the next chapter still runs
        return row | {"error": str(e)[:300], "scenes": []}
    scenes = [await _collect_scene(ctx, s["id"]) for s in started["scenes"]]
    return row | {
        "queued": started.get("queued"),
        "deferred": started.get("deferred"),
        "reason": started.get("reason"),
        "total": started.get("total"),
        "scenes": [
            {k: s.get(k) for k in ("id", "status", "content", "planned_beat_id")}
            for s in scenes
        ],
        "words": sum(len((s.get("content") or "").split()) for s in scenes),
    }


@step(STEP, needs_api=True, repeatable=False)
async def step_chapters(ctx: StepContext) -> dict:
    k = chapter_count()
    book = await _book_with_cast(ctx, eval_title(ctx.book, STEP))
    names = {cid: name for name, cid in book["character_ids"].items()}
    voice = None
    if ctx.ground_truth.get("gold_lines"):  # as step_prose: the product's retrieval
        voice = await _index_voice(ctx, book["id"], list(book["character_ids"]))
    prior = prior_outline(ctx.book)
    if prior:
        outline, source = prior
    else:
        outline, source = (await _generate_outline(ctx, book))["portable"], "generated"
    if not outline:
        return {"score": None, "error": "no outline to promote"}
    await ctx.client.put_outline(book["id"], outline)  # both releases: this form
    promoted = await ctx.client.promote_all(book["id"])
    chapters = sorted(
        promoted.get("chapters") or [], key=lambda c: c.get("position") or 0
    )
    rows = [await _write_chapter(ctx, ch, names) for ch in chapters[:k]]
    written = sum(
        1 for r in rows if any((s.get("content") or "").strip() for s in r["scenes"])
    )
    return {
        "score": None,  # material for chapters_compare, not a quality score
        "judge_explanation": f"{STEP}: {written}/{len(rows)} chapters written "
        f"(outline {source}); judge with chapters_compare",
        "outline": outline,
        "outline_digest": digest(outline),
        "outline_source": source,
        "voice_indexed": bool(voice and voice.get("indexed")),
        "chapters_asked": k,
        "chapters": rows,
    }
