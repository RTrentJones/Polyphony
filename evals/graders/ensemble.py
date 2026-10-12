"""Ensemble vs prose grader (SPEC-14).

Deterministic signals for the claim that justifies the ensemble's cost: that
every present character visibly ACTS (BRD R6.1, R6.3) and that their lines stay
distinct. `action_coverage` is the fraction of the scene's cast with at least
one sentence outside quotation marks that names them — narration of what they
do, not just dialogue tags.
"""

from __future__ import annotations

import re

_QUOTED = re.compile(r"[\"\u201c][^\"\u201d]*[\"\u201d]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _forms(name: str) -> list[str]:
    toks = [t for t in name.split() if len(t) > 1]
    return [name, *toks] if len(toks) > 1 else [name]


def action_coverage(prose: str, cast: list[str]) -> float:
    """Fraction of `cast` with a non-dialogue sentence that names them."""
    if not cast:
        return 0.0
    # A sentence carrying dialogue ('"Go," Milo said.') is a speech tag, not an
    # action, so only quote-free sentences count as narration.
    sentences = [
        s
        for s in _SENTENCE.split(prose or "")
        if s.strip() and not _QUOTED.search(s) and not re.search('["\u201c\u201d]', s)
    ]
    acting = 0
    for name in cast:
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(f) for f in _forms(name)) + r")\b"
        )
        if any(pattern.search(s) for s in sentences):
            acting += 1
    return round(acting / len(cast), 4)
