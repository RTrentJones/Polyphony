"""Named secondary people: in the synopsis/canon, not in the cast (SPEC-24).

The author's synopsis names people who are not (yet) in the cast: Prava, the
intake caseworker; Old Fen in Records. The outline may name them (the prompt
says so in one sentence); the model lists everyone a chapter or beat names in
its `people`, and outline.classify_people keeps the ones the canon names as
`secondary_names`. `suggested_characters` is what the author is then offered
("Add Prava to cast"): only names the MODEL declared, re-classified against
today's cast and canon. A name absent from both the synopsis and the canon is
never offered, and nothing here guesses who is a person from the canon text.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from app.parsing.names import mentions, norm
from app.planning.fidelity import canon_known_text

SENTENCE_CAP = 400

_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])[\"”’'\)\]]*\s+(?=[\"“‘'(\[]?[A-Z0-9])")
_PARAGRAPH = re.compile(r"\n[ \t]*\n")


def _paragraphs(text: str) -> list[str]:
    """Paragraphs with hard line wraps undone, so a name wrapped across two
    lines ("There is\\nOld Fen") is one run and a wrapped line start is not
    mistaken for a sentence start."""
    return [
        " ".join(p.split())
        for p in _PARAGRAPH.split((text or "").replace("\r\n", "\n"))
        if p.strip()
    ]


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for paragraph in _paragraphs(text):
        starts = [0] + [m.end() for m in _SENTENCE_BREAK.finditer(paragraph)]
        bounds = zip(starts, starts[1:] + [len(paragraph)])
        out.extend(s for a, b in bounds if (s := paragraph[a:b].strip()))
    return out


def _cap(sentence: str, limit: int = SENTENCE_CAP) -> str:
    if len(sentence) <= limit:
        return sentence
    cut = sentence[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:—-") + "…"


def _first_sentence_naming(name: str, texts: Iterable[str]) -> Optional[str]:
    for text in texts:
        for sentence in _sentences(text):
            if mentions(norm(sentence), name):
                return _cap(sentence)
    return None


def suggested_characters(names: Iterable[str], canon) -> list[dict]:
    """[{"name", "synopsis_sentence"}]: each of `names` that classify_people
    (against canon's current cast, with known_text from canon) puts in
    `secondary`, deduped case-insensitively, in the given order.
    synopsis_sentence is the first synopsis sentence naming them, else the
    first canon-entry sentence naming them, capped at 400 chars.

    The caller collects `names` (the declared `secondary_names` of the outline
    and its promoted chapters; app/api/plans.py)."""
    from app.planning.outline import CastIndex, classify_people

    cast = CastIndex(getattr(canon, "characters", None) or [])
    people = classify_people(names, cast, norm(canon_known_text(canon)))
    synopsis = getattr(canon, "synopsis", "") or ""
    entry_texts = [
        str(getattr(e, "content", "") or "")
        for e in getattr(canon, "canon_entries", None) or []
    ]
    return [
        {
            "name": name,
            "synopsis_sentence": _first_sentence_naming(name, [synopsis])
            or _first_sentence_naming(name, entry_texts)
            or "",
        }
        for name in people.secondary
    ]
