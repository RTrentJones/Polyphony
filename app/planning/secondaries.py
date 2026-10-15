"""Named secondary people: in the synopsis/canon, not in the cast (SPEC-24, W2-2).

The author's synopsis names people who are not (yet) in the cast: Prava, the
intake caseworker; Old Fen in Records. The outline used to be forbidden to
name them, so it wrote "the caseworker" instead. Two helpers, both
deterministic:

- `secondary_candidates(canon)` feeds the outline prompts' SECONDARY PEOPLE
  line: the capitalised names the canon text uses that are not cast. It is a
  heuristic (it cannot tell a person from a place), so it only ever widens
  what the model MAY name; it never becomes data on its own.
- `suggested_characters(nodes, canon)` is what the author is offered ("Add
  Prava to cast"): only names the MODEL declared as a node's or beat's
  `secondary_names`, filtered to the canon text and to people not already in
  the cast. A name absent from both the synopsis and the canon is never
  offered.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.planning.fidelity import (
    _SKIP_WORDS,
    _candidates,
    _norm,
    _present,
    canon_known_text,
)

SENTENCE_CAP = 400

# A bible line opens with a field label ("Description: ...", "Also known as:");
# the label is structure, not a name.
_LABEL = re.compile(r"^[A-Z][A-Za-z ]{0,30}:\s+")
_ALL_CAPS = re.compile(r"^[A-Z]{2,}$")
_LOWER_WORD = re.compile(r"\b[a-z][a-z'-]*\b")
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


def _bible_lines(bible: str) -> list[str]:
    """The bible's content lines: headings dropped, field labels stripped."""
    out = []
    for line in (bible or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(_LABEL.sub("", line))
    return out


def _segments(canon) -> list[str]:
    """canon_known_text(canon), cut where a sentence or a field starts."""
    segments = _paragraphs(getattr(canon, "synopsis", "") or "")
    bible = getattr(canon, "bible", None)
    if callable(bible):
        segments.extend(_bible_lines(bible() or ""))
    return segments


def _tokens(phrase: str) -> set[str]:
    return {t for t in _norm(phrase).split() if len(t) > 1 and t not in _SKIP_WORDS}


def _cast_forms(canon) -> tuple[set[str], set[str]]:
    """(full names and aliases, their significant tokens) of the cast."""
    forms: set[str] = set()
    for c in getattr(canon, "characters", None) or []:
        for form in [getattr(c, "name", None), *(getattr(c, "aliases", None) or [])]:
            if isinstance(form, str) and _norm(form):
                forms.add(_norm(form))
    tokens = {
        t for f in forms for t in f.split() if len(t) > 1 and t not in _SKIP_WORDS
    }
    return forms, tokens


def _within(norm: str, phrases: Iterable[str]) -> bool:
    """`norm` is one of `phrases`, a run of words inside one, or contains one."""
    for p in phrases:
        if not p:
            continue
        if norm == p or _present(norm, p) or _present(p, norm):
            return True
    return False


def secondary_candidates(canon) -> list[str]:
    """Capitalised names in canon_known_text(canon) that are not cast names or
    aliases, canon-entry names, the book title, or sentence-initial common words.
    Ordered by first appearance. Used in the outline prompts."""
    segments = _segments(canon)
    if not segments:
        return []
    text = "\n".join(segments)
    lower_words = set(_LOWER_WORD.findall(text))
    cast_forms, cast_tokens = _cast_forms(canon)
    entries = [
        _norm(getattr(e, "name", "") or "")
        for e in getattr(canon, "canon_entries", None) or []
    ]
    title = _norm(getattr(canon, "title", "") or "")

    found: dict[str, str] = {}
    mid_sentence: set[str] = set()
    for segment in segments:
        for words, initial in _candidates(segment):
            if any(_ALL_CAPS.match(w) for w in words):
                continue  # a heading ("PART SIX") or an acronym
            if any(w.lower() in _SKIP_WORDS for w in words):
                continue  # a title-case fragment, not a name
            norm = _norm(" ".join(words))
            if not norm:
                continue
            if not initial:
                mid_sentence.add(norm)
            found.setdefault(norm, " ".join(words))

    out: list[str] = []
    for norm, surface in found.items():
        words = norm.split()
        if all(w in lower_words for w in words):
            continue  # common words, capitalised ("Intake", "Records")
        if len(words) == 1 and norm not in mid_sentence:
            continue  # only ever opens a sentence ("Death, it turns out")
        if norm in cast_forms or _tokens(norm) & cast_tokens:
            continue  # the cast, or a phrase built on a cast name
        if _within(norm, entries) or (title and _within(norm, [title])):
            continue
        out.append(surface)
    # "Fen" is Old Fen and "Aeon" is Aeon Holdings: list the fuller name once.
    norms = [_norm(s) for s in out]
    return [
        s
        for s, n in zip(out, norms)
        if not any(o != n and _present(n, o) for o in norms)
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
            if _present(name, _norm(sentence)):
                return _cap(sentence)
    return None


def _walk(nodes: Any):
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict):
            yield node
            yield from _walk(node.get("children"))


def suggested_characters(nodes: list[dict], canon) -> list[dict]:
    """[{"name": str, "synopsis_sentence": str}]: every `secondary_names` entry
    on any outline node or child that (a) occurs in canon_known_text(canon) and
    (b) does not resolve to the CURRENT cast (CastIndex). Deduped, case-insensitive,
    in outline order. synopsis_sentence is the first sentence of the synopsis
    naming them, else the first sentence of the canon entries naming them,
    capped at 400 chars."""
    from app.planning.outline import CastIndex

    hay = _norm(canon_known_text(canon))
    if not hay:
        return []
    cast = CastIndex(getattr(canon, "characters", None) or [])
    synopsis = getattr(canon, "synopsis", "") or ""
    entry_texts = [
        str(getattr(e, "content", "") or "")
        for e in getattr(canon, "canon_entries", None) or []
    ]
    seen: set[str] = set()
    out: list[dict] = []
    for node in _walk(nodes):
        names = node.get("secondary_names")
        for raw in names if isinstance(names, list) else []:
            name = str(raw or "").strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            if not _present(name, hay) or cast.resolve(name) is not None:
                continue
            sentence = (
                _first_sentence_naming(name, [synopsis])
                or _first_sentence_naming(name, entry_texts)
                or ""
            )
            out.append({"name": name, "synopsis_sentence": sentence})
    return out
