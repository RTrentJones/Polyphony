"""Named secondary people: in the synopsis/canon, not in the cast (SPEC-24, W2-2)."""

from __future__ import annotations


def secondary_candidates(canon) -> list[str]:
    """Capitalised names in canon_known_text(canon) that are not cast names or
    aliases, canon-entry names, the book title, or sentence-initial common words.
    Ordered by first appearance. Used in the outline prompts."""
    return []


def suggested_characters(nodes: list[dict], canon) -> list[dict]:
    """[{"name": str, "synopsis_sentence": str}]: every `secondary_names` entry
    on any outline node or child that (a) occurs in canon_known_text(canon) and
    (b) does not resolve to the CURRENT cast (CastIndex). Deduped, case-insensitive,
    in outline order. synopsis_sentence is the first sentence of the synopsis
    naming them, else the first sentence of the canon entries naming them,
    capped at 400 chars."""
    return []
