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

EVAL_CHAPTERS=0 makes an outline-only run (the book's outline day). With
EVAL_CHAPTER_REPLICATES=R the K chapters are written R times in one run: each
replicate is a continuous attempt at chapters 1..K, recorded, then its scenes
are deleted (trashed) so the next replicate writes from the same plan.

Requests are counted from the app's /usage (calls by purpose, this run's user):
the setup's, and each chapter's, so the Flash-Lite day budget can be planned
from measured numbers. Before each chapter the run stops cleanly when the
generator's requests left today are under EVAL_CHAPTER_RESERVE (default 40):
a chapter that hits the provider's daily cap would pause, not finish.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
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
DEFAULT_RESERVE = 40
# A chapter that did not finish for quota or provider availability (a 429 at
# enqueue, a daily limit, a job left paused until the poll timed out, an
# unavailable provider) says nothing about the release: compare excludes the
# pair on both sides. Anything else that left no text is the product's.
_QUOTA = re.compile(
    r"\b429\b|\b503\b|quota|rate.?limit|DailyRequestLimit|per.?day|paused|"
    r"poll timed out|ProviderUnavailable|unavailable",
    re.I,
)


def failure(
    error: str | None, scenes: list[dict], deferred=None, total=None
) -> str | None:
    """`quota`, `product` or None, from every scene's status, never from text
    presence alone (generate_planned settles as soon as a scene fails, so a
    chapter can carry an earlier scene's text and still be truncated).

    Quota wins: an error, a deferred beat, a paused scene or a quota-marked
    scene error is `quota`. Otherwise any scene not completed with text, or
    fewer completed scenes than the chapter's planned beats (`total`: the
    chain writes scene k+1 only after scene k completes), is `product`.
    None only when every planned scene completed with text."""
    if error is not None:
        return "quota" if _QUOTA.search(error) else "product"
    if deferred:  # the day's token budget held beats back (SPEC-34)
        return "quota"
    errs = " ".join(str(s.get("error") or "") for s in scenes)
    if any(s.get("status") == "paused" for s in scenes) or _QUOTA.search(errs):
        return "quota"
    done = [
        s
        for s in scenes
        if s.get("status") == "completed" and (s.get("content") or "").strip()
    ]
    if not scenes or len(done) < len(scenes):
        return "product"
    if isinstance(total, int) and len(done) < total:
        return "product"
    return None


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


def _bounded(var: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(var)
    n = int(raw) if raw not in (None, "") else default
    if not lo <= n <= hi:
        raise SystemExit(f"{var} must be {lo}..{hi}, got {n}")
    return n


def chapter_count() -> int:
    """K; 0 = an outline-only run."""
    return _bounded("EVAL_CHAPTERS", DEFAULT_CHAPTERS, 0, 12)


def replicate_count() -> int:
    return _bounded("EVAL_CHAPTER_REPLICATES", 1, 1, 6)


async def _calls(ctx: StepContext) -> int:
    """This run's user's LLM calls so far (GET /usage, by purpose)."""
    u = await ctx.client.usage()
    return sum(int(r.get("calls") or 0) for r in u.get("by_purpose_30d") or [])


async def _left_today(ctx: StepContext) -> int | None:
    """Requests left today on the generator's model (None: not tracked)."""
    from evals.harness.config import generator

    model = generator().get("model")
    row = ((await ctx.client.usage()).get("models") or {}).get(model) or {}
    left = row.get("left")
    return left if isinstance(left, int) else None


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
        return row | {
            "error": str(e)[:300],
            "failure": failure(str(e), []),
            "scenes": [],
        }
    scenes = [await _collect_scene(ctx, s["id"]) for s in started["scenes"]]
    return row | {
        "failure": failure(None, scenes, started.get("deferred"), started.get("total")),
        "queued": started.get("queued"),
        "deferred": started.get("deferred"),
        "reason": started.get("reason"),
        "total": started.get("total"),
        "scenes": [
            {
                k: s.get(k)
                for k in ("id", "status", "content", "planned_beat_id", "error")
            }
            for s in scenes
        ],
        "words": sum(len((s.get("content") or "").split()) for s in scenes),
    }


async def _trash_replicate(ctx: StepContext, chapters: list[dict]) -> None:
    """Every live scene of these chapters, read back from the app: a paused or
    failed leftover of an errored chapter included, so none of it reaches the
    next replicate's context."""
    for ch in chapters:
        detail = await ctx.client.get_chapter(ch["chapter_id"])
        for sc in detail.get("scenes") or []:
            await ctx.client.delete_scene(sc["id"])


@step(STEP, needs_api=True, repeatable=False)
async def step_chapters(ctx: StepContext) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    k, reps = chapter_count(), replicate_count()
    reserve = _bounded("EVAL_CHAPTER_RESERVE", DEFAULT_RESERVE, 0, 500)
    calls0 = await _calls(ctx)
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
    setup_calls = await _calls(ctx) - calls0
    rows, stopped = [], None
    for rep in range(1, reps + 1) if k else ():
        for ch in chapters[:k]:
            left = await _left_today(ctx)
            if left is not None and left < reserve:
                stopped = f"{left} generator requests left today (< {reserve})"
                break
            before = await _calls(ctx)
            row = await _write_chapter(ctx, ch, names)
            rows.append(row | {"replicate": rep, "calls": await _calls(ctx) - before})
        if stopped:
            break
        if rep < reps:  # the next replicate writes the same plan afresh
            await _trash_replicate(ctx, chapters[:k])
    written = sum(
        1 for r in rows if any((s.get("content") or "").strip() for s in r["scenes"])
    )
    return {
        "score": None,  # material for chapters_compare, not a quality score
        "judge_explanation": f"{STEP}: {written}/{len(rows)} chapters written "
        f"(outline {source}, {reps} replicate(s))"
        + (f"; stopped: {stopped}" if stopped else "")
        + "; judge with chapters_compare",
        "outline": outline,
        "outline_digest": digest(outline),
        "outline_source": source,
        # Names this run's chapters: compare pairs one day's two runs as a batch.
        "batch": uuid.uuid4().hex[:12],
        # SPEC-96: the run's day, for pairing arms dispatched the same day.
        "started_at": started,
        "voice_indexed": bool(voice and voice.get("indexed")),
        "chapters_asked": k,
        "replicates_asked": reps,
        "stopped": stopped,
        "requests": {
            "setup": setup_calls,
            "per_chapter": [r["calls"] for r in rows],
            "left_today_at_end": await _left_today(ctx),
        },
        "chapters": rows,
    }
