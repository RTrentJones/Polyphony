"""Honorific-aware character-name matching (SPEC-09).

One implementation shared by extraction's cross-window merge and the eval
grader (`evals/graders/extraction.py`), so the rule the evals prove is the rule
the product runs: "Prof. Verhoeven" ~ "Verhoeven", "Mrs. Harker" ~ "Mina
Harker", but "John Ward" !~ "Elias Ward".
"""

from __future__ import annotations

import re

HONORIFICS = {
    "prof",
    "professor",
    "dr",
    "doctor",
    "mr",
    "mister",
    "mrs",
    "ms",
    "miss",
    "madam",
    "madame",
    "count",
    "countess",
    "lord",
    "lady",
    "sir",
    "master",
    "st",
    "saint",
    "hon",
}

_PUNCT = re.compile(r"[^\w\s'-]+")


def norm(name: str) -> str:
    """Lowercase, punctuation-light, whitespace-collapsed (so "Count\\nVasska"
    and "Count Vasska" are one key)."""
    s = (name or "").lower().replace(".", " ").replace("’", "'")
    return " ".join(_PUNCT.sub(" ", s).split())


def tokens(name: str) -> set[str]:
    return set(norm(name).split())


def significant(name: str) -> set[str]:
    """Tokens minus honorifics; empty for a bare honorific ("Count", "Dr.")."""
    return tokens(name) - HONORIFICS


def match(a: str, b: str) -> bool:
    """One name's tokens are a SUBSET of the other's — after dropping honorifics
    or on the raw tokens. Subset, not overlap, so two people sharing a surname
    do not match."""
    a_full, b_full = tokens(a), tokens(b)
    if not a_full or not b_full:
        return False
    a_sig = (a_full - HONORIFICS) or a_full
    b_sig = (b_full - HONORIFICS) or b_full
    return a_sig <= b_sig or b_sig <= a_sig or a_full <= b_full or b_full <= a_full
