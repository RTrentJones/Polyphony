"""Named secondary people: in the synopsis/canon, not in the cast (SPEC-24).

The author's synopsis names people who are not (yet) in the cast: Prava, the
intake caseworker; Old Fen in Records. The outline may name them (the prompt
says so in one sentence); the model lists everyone a chapter or beat names in
its `people`, and outline.classify_people keeps the ones the canon names as
`secondary_names`. `suggested_characters` is what the author is then offered
("Add Prava to cast"): only names the MODEL declared, re-classified against
today's cast and canon. A name absent from both the synopsis and the canon is
never offered, and nothing here guesses who is a person from the canon text.
A scene's secondaries reach its prompts as one fenced block (SPEC-29).
"""

from __future__ import annotations

import re
from typing import Iterable, NamedTuple

from app.core.llm_text import as_quoted_block
from app.core.prompt_markers import (
    INTRODUCED_LABEL,
    NAME_HEADING,
    SECONDARY_FENCE,
    VOICE_SAMPLES_LABEL,
)
from app.parsing.names import mentions, norm
from app.planning.fidelity import canon_known_text

SENTENCE_CAP = 400
SAMPLES = 3
NO_SAMPLES_NOTE = "No samples: keep them brief and distinct from the cast."

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


def intro_sentence(name: str, texts: Iterable[str]) -> str:
    """The first sentence of `texts` that names `name`, capped; "" if none."""
    for text in texts:
        for sentence in _sentences(text):
            if mentions(norm(sentence), name):
                return _cap(sentence)
    return ""


class Secondary(NamedTuple):
    name: str
    intro_sentence: str  # "" when nothing names them
    samples: list[str]  # <= SAMPLES of their own lines from the sources


async def scene_secondaries(
    session, book_id, names: list[str], canon
) -> list[Secondary]:
    """Each name's introduction and own lines (SPEC-17 attribution, each live
    source attributed once; lines of 4+ words first)."""
    from sqlalchemy import select

    from app.core.orm_models import Source
    from app.parsing import character_extractor as ce

    if not names:
        return []
    rows = await session.execute(
        select(Source.content_text)
        .where(Source.book_id == book_id, Source.deleted_at.is_(None))
        .order_by(Source.uploaded_at)
    )
    texts = [t for t in rows.scalars() if t]
    attributed = [ce.attribute_speech(t) for t in texts]
    cast = [{"name": c.name, "aliases": c.aliases or []} for c in canon.characters]
    entries = [str(getattr(e, "content", "") or "") for e in canon.canon_entries or []]
    out = []
    for name in names:
        said = [
            line
            for text, spans in zip(texts, attributed)
            for _, line in ce.spoken_lines(text, name, cast=cast, attributed=spans)
            if len(line.split()) >= ce._SPEECH_MIN_WORDS
        ]
        said = sorted(dict.fromkeys(said), key=lambda line: len(line.split()) < 4)
        intro = intro_sentence(name, [canon.synopsis or "", *entries, *texts])
        out.append(Secondary(name, intro, said[:SAMPLES]))
    return out


def render_secondaries(people: list[Secondary]) -> str:
    """The `<secondary>` block; no bible fields (the canon carries them)."""
    lines: list[str] = []
    for p in people:
        lines.append(f"{NAME_HEADING}{p.name}")
        if p.intro_sentence:
            lines.append(f"{INTRODUCED_LABEL} {p.intro_sentence}")
        if p.samples:
            lines += [VOICE_SAMPLES_LABEL, *(f'- "{s}"' for s in p.samples)]
        else:
            lines.append(NO_SAMPLES_NOTE)
    return as_quoted_block("\n".join(lines), SECONDARY_FENCE)


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
            "synopsis_sentence": intro_sentence(name, [synopsis])
            or intro_sentence(name, entry_texts),
        }
        for name in people.secondary
    ]
