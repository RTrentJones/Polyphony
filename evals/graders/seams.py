"""Seam grader: repeated text where two generated pieces meet.

A scene is written beat by beat and the beats are joined with blank lines, so a
model that re-tells the end of the previous beat (or the previous scene) leaves
the repeat in the author's draft (backlog B5). This measures it without a
judge: the word 6-grams two ADJACENT pieces share. A clean seam shares none.

    max_overlap(["...", "...", ...]) -> the most 6-grams any adjacent pair shares

Words are compared lowercased with punctuation stripped, so a repeat that only
differs by a comma or a curly quote still counts.
"""

from __future__ import annotations

import re

N = 6
SEAM_WORDS = 60

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def words(text: str) -> list[str]:
    """Normalised words: lowercase, apostrophes kept inside words, curly quotes
    folded to straight ones, everything else dropped."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    return _WORD.findall(t)


def _grams(w: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def shared(a: str, b: str, n: int = N) -> set[tuple[str, ...]]:
    """The n-grams the end of `a` and the start of `b` have in common: the
    seam where `b` follows `a`."""
    return _grams(words(a)[-SEAM_WORDS:], n) & _grams(words(b)[:SEAM_WORDS], n)


def max_overlap(texts: list[str], n: int = N) -> int:
    """The largest number of n-grams repeated across any seam between two
    ADJACENT texts (0 when fewer than two texts). 0 is a clean set of seams."""
    return max(
        (len(shared(a, b, n)) for a, b in zip(texts, texts[1:])),
        default=0,
    )


def seam_report(texts: list[str], n: int = N) -> dict:
    """Per adjacent pair: how many n-grams repeat, with one example, plus the
    max. `pairs[i]` is the seam between texts[i] and texts[i + 1]."""
    pairs = []
    for i, (a, b) in enumerate(zip(texts, texts[1:])):
        common = shared(a, b, n)
        pairs.append(
            {
                "seam": i,
                "shared": len(common),
                "example": " ".join(sorted(common)[0]) if common else None,
            }
        )
    return {
        "n": n,
        "seams": len(pairs),
        "max_overlap": max((p["shared"] for p in pairs), default=0),
        "dirty_seams": sum(1 for p in pairs if p["shared"]),
        "pairs": pairs,
    }
