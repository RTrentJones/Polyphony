"""Extraction preflight: what a run will cost, and whether it can finish (SPEC-09).

Every endpoint that enqueues an extraction returns this estimate (R7.5), and
refuses when the run cannot finish within what the author can spend while the
job waits out quota pauses (`JOB_MAX_PAUSE_HOURS`, SPEC-07) — offering "extract
the first N chapters" instead (R7.3). Refusing up front beats a job that pauses
for days and then dies with half the windows read.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.budget import remaining_budget_24h
from app.core.config import settings
from app.parsing.canon_extractor import plan_estimate

DEFAULT_MAX_PAUSE_HOURS = 48

# A chapter heading on its own line: "CHAPTER IV", "Chapter 12: The Sea",
# "BOOK TWO", "Part 3", "Letter 4". Deliberately a heading, never a sentence.
_CHAPTER = re.compile(
    r"^[ \t]*(?:chapter|book|part|letter|act)\b[ \t]+"
    r"(?:[ivxlcdm]+|\d+|[a-z]+(?:-[a-z]+)?)\b[^\n]{0,80}$",
    re.I | re.M,
)


def chapter_starts(text: str) -> list[int]:
    """Offsets of chapter headings, in order (empty when the text has none)."""
    return [m.start() for m in _CHAPTER.finditer(text or "")]


def first_chapters(text: str, n: int) -> str:
    """The text through the end of chapter `n` — the author's explicit choice of
    scope, cut on a chapter boundary (never mid-paragraph). Text with fewer than
    n+1 chapter headings is returned whole."""
    starts = chapter_starts(text)
    if n <= 0 or len(starts) <= n:
        return text
    return text[: starts[n]].rstrip()


def _max_pause_hours() -> float:
    return float(
        getattr(settings, "JOB_MAX_PAUSE_HOURS", None) or DEFAULT_MAX_PAUSE_HOURS
    )


async def preflight_extraction(
    db: AsyncSession, user_id: UUID, text: str, *, max_chapters: int | None = None
) -> dict:
    """The estimate for extracting `text` and whether it fits.

    Capacity = what is left of today's budget + one daily allowance per day the
    job may sit paused waiting for quota. Returns {"fits", windows, requests,
    estimated_tokens, capacity_tokens, and — when refused — "max_chapters"
    (the largest opening that fits, or None when the text has no chapters)}.
    """
    from app.llm.tier import get_tier

    scoped = first_chapters(text, max_chapters) if max_chapters else text
    est = plan_estimate(scoped)
    limit = get_tier().daily_token_limit
    remaining = await remaining_budget_24h(db, user_id)
    if not limit:
        capacity = remaining
    else:
        capacity = remaining + int(limit * _max_pause_hours() / 24)
    out = {
        **est,
        "capacity_tokens": capacity,
        "fits": est["estimated_tokens"] <= capacity,
        "max_chapters": max_chapters,
    }
    if not out["fits"]:
        starts = chapter_starts(scoped)
        best = None
        for n in range(1, len(starts)):
            if plan_estimate(first_chapters(scoped, n))["estimated_tokens"] > capacity:
                break
            best = n
        out["max_chapters"] = best
        out["message"] = (
            f"Extracting this source needs about {est['estimated_tokens']:,} tokens "
            f"over {est['windows']} parts, more than the {capacity:,} you can spend "
            f"within {int(_max_pause_hours())}h. "
            + (
                f"Extract the first {best} chapters instead."
                if best
                else "Split the source into smaller pieces and extract each."
            )
        )
    return out
