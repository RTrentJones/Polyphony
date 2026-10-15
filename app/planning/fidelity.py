"""Deterministic fidelity audit for a generated outline (docs/BRD.md R1.4-R1.6).

The app-side twin of the eval grader: it runs at GENERATION time so an unfaithful
outline is caught and repaired before it is ever saved, not just measured after
the fact. Deterministic and free — no LLM, no NLP.

- principal_recall is the HARD gate: every principal in the canon must appear
  (exact, word-boundary, alias-aware). recall < 1.0 -> regenerate once, then warn.
- unknown proper nouns are a SOFT warning only: a good outline may invent a minor
  name, so a heuristic must never hard-fail an expensive job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_STOP_CAPS = {
    "the",
    "a",
    "an",
    "act",
    "chapter",
    "part",
    "book",
    "scene",
    "prologue",
    "epilogue",
    "he",
    "she",
    "they",
    "it",
    "his",
    "her",
    "their",
    "when",
    "then",
    "after",
    "before",
    "as",
    "but",
    "and",
    "or",
    "in",
    "on",
    "at",
    "with",
    "for",
    "to",
    "from",
    "meanwhile",
    "finally",
    "later",
    "now",
    "here",
    "there",
    "this",
    "that",
    "these",
    "those",
    "one",
    "two",
    "three",
    "four",
    "five",
}
# Function words that are lowercase in running prose. Capitalised, they mark a
# title-case fragment ("Meeting That Could Have Been") or a sentence opener
# ("Under Zara's training"), never part of a name.
_CONNECTIVES = {
    "about",
    "across",
    "against",
    "ago",
    "all",
    "am",
    "are",
    "be",
    "been",
    "being",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "every",
    "had",
    "has",
    "have",
    "if",
    "into",
    "is",
    "its",
    "may",
    "might",
    "must",
    "no",
    "not",
    "onward",
    "out",
    "over",
    "shall",
    "should",
    "so",
    "than",
    "through",
    "under",
    "until",
    "up",
    "upon",
    "was",
    "were",
    "what",
    "where",
    "which",
    "who",
    "why",
    "will",
    "would",
    "yet",
}
# Calendar words are always capitalised but never invented names.
_CALENDAR = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
_SKIP_WORDS = _STOP_CAPS | _CONNECTIVES | _CALENDAR
# Capitalised runs joined by a SINGLE space: a run never spans a field boundary
# (fields are joined with two spaces), a comma or a line break, so "Debt" ending
# one title and "In" opening the next summary are two words, not "Debt  In".
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:[ ][A-Z][a-z]+)*\b|\b[A-Z]{2,}\b")
_WORD = re.compile(r"[A-Za-z]+")
_OPENERS = '.!?;:"“(—'


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").split())


def _outline_fields(nodes: list[dict]) -> tuple[list[str], list[str]]:
    """(titles, everything else) in walk order: the text an outline shows."""
    titles: list[str] = []
    body: list[str] = []

    def walk(node: dict) -> None:
        for key in ("title", "summary", "pov", "premise_restated", "central_conflict"):
            val = node.get(key)
            if isinstance(val, str):
                (titles if key == "title" else body).append(val)
        for key in ("characters", "threads"):
            val = node.get(key)
            if isinstance(val, list):
                body.extend(str(v) for v in val)
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                walk(child)

    for n in nodes:
        if isinstance(n, dict):
            walk(n)
    return titles, body


def canon_known_text(canon) -> str:
    """The canon's authored text (synopsis + cast + entries + style): every
    capitalised phrase in it is a known name, not an outline invention."""
    parts = [getattr(canon, "synopsis", "") or ""]
    bible = getattr(canon, "bible", None)
    if callable(bible):
        parts.append(bible() or "")
    return "\n".join(p for p in parts if p)


def flatten_outline(nodes: list[dict]) -> str:
    """All human-readable text in an outline: titles, summaries, pov, cast names."""
    parts: list[str] = []

    def walk(node: dict) -> None:
        for key in ("title", "summary", "pov", "premise_restated", "central_conflict"):
            val = node.get(key)
            if isinstance(val, str):
                parts.append(val)
        for key in ("characters", "threads"):
            val = node.get(key)
            if isinstance(val, list):
                parts.extend(str(v) for v in val)
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                walk(child)

    for n in nodes:
        if isinstance(n, dict):
            walk(n)
    return "  ".join(parts)


def _present(needle: str, hay_norm: str) -> bool:
    n = _norm(needle)
    return bool(n) and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", hay_norm) is not None


def principal_recall(
    outline_text: str, principals: list[dict]
) -> tuple[float, list[str], list[str]]:
    """Fraction of canon principals present by name OR any alias."""
    if not principals:
        return 1.0, [], []
    hay = _norm(outline_text)
    present, missing = [], []
    for p in principals:
        forms = [p["name"], *p.get("aliases", [])]
        (present if any(_present(f, hay) for f in forms) else missing).append(p["name"])
    return round(len(present) / len(principals), 4), present, missing


def _is_sentence_initial(text: str, start: int) -> bool:
    """True when the match at `start` opens a field or a sentence."""
    if start == 0:
        return True
    window = text[max(0, start - 4) : start]
    if window.endswith("  "):  # flatten_outline's field separator
        return True
    stripped = window.rstrip()
    if not stripped:
        return start <= len(window)
    return stripped[-1] in _OPENERS


def _candidates(text: str):
    """Yield (words, sentence_initial) for each capitalised run in `text`, with
    leading and trailing function words ("The", "Under", "At") trimmed."""
    for m in _PROPER.finditer(text or ""):
        words = m.group(0).split(" ")
        initial = _is_sentence_initial(text, m.start())
        while words and words[0].lower() in _SKIP_WORDS:
            words.pop(0)
            initial = False  # what is left no longer opens the sentence
        while words and words[-1].lower() in _SKIP_WORDS:
            words.pop()
        if words:
            yield words, initial


def unknown_proper_nouns(
    outline_text: str, known: set[str], *, canon_text: str = ""
) -> list[str]:
    """Capitalised phrases in `outline_text` that the canon does not name.

    A soft heuristic, tuned to stay quiet on ordinary prose so the warning is
    worth reading. A phrase is NOT reported when
    - it, or every word in it, is named in `known` or capitalised anywhere in
      `canon_text` (synopsis, cast, canon entries, style);
    - it is a title-case fragment, i.e. carries a function word that is
      lowercase in prose ("Meeting That Could Have Been");
    - every word in it is also used in lowercase somewhere in the outline or
      the canon (a common word capitalised by a heading or a sentence: "Desk");
    - it is one sentence-opening word with a common-word ending ("Following").
    `audit_outline` passes outline text WITHOUT chapter/beat titles, so a
    phrase that only ever appears inside a title is skipped too.
    """
    known_norm = {_norm(k) for k in known if _norm(k)}
    for words, _initial in _candidates(canon_text):
        known_norm.add(_norm(" ".join(words)))
    known_tokens = {tok for k in known_norm for tok in k.split()}
    lower_words = {
        w for w in _WORD.findall(f"{outline_text}\n{canon_text}") if w.islower()
    }

    found: dict[str, str] = {}
    for words, initial in _candidates(outline_text):
        lowered = [w.lower() for w in words]
        if any(w in _SKIP_WORDS for w in lowered):
            continue  # title-case fragment
        if all(w in lower_words for w in lowered):
            continue  # common words, capitalised
        if initial and len(words) == 1 and lowered[0].endswith(("ing", "ed", "ly")):
            continue
        norm = _norm(" ".join(words))
        if norm:
            found.setdefault(norm, " ".join(words))
    unknown = [
        surface
        for norm, surface in found.items()
        if not (
            all(tok in known_tokens for tok in norm.split())
            or any(
                norm == k or norm in k.split() or k in norm.split() for k in known_norm
            )
        )
    ]
    return sorted(unknown)


@dataclass
class FidelityAudit:
    principal_recall: float
    present: list[str]
    missing: list[str]
    unknown: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """The HARD gate: every principal present."""
        return self.principal_recall >= 1.0

    def as_dict(self) -> dict:
        return {
            "principal_recall": self.principal_recall,
            "present": self.present,
            "missing": self.missing,
            "unknown": self.unknown,
            "warnings": self.warnings,
        }


def _structural_cast(nodes: list[dict], cast) -> tuple[list[str], list[str]]:
    """(names the top-level structural fields resolve to, entries that resolve
    to nobody in the cast). `pov` and `characters`/`character_ids` are STRICT
    cast (R1.5): they name who is in a chapter, so an entry outside the canon is
    reported per chapter, never left to the advisory proper-noun heuristic."""
    names: list[str] = []
    bad: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        refs = node.get("character_ids")
        if not isinstance(refs, list):
            refs = node.get("characters")
        refs = list(refs) if isinstance(refs, list) else []
        if isinstance(node.get("pov"), str) and node["pov"].strip():
            refs.append(node["pov"])
        outside = []
        for ref in refs:
            cid = cast.resolve(ref)
            if cid is None:
                outside.append(str(ref))
            else:
                names.append(cast.names[cid])
        if outside:
            bad.append(f"{node.get('title', '?')}: {', '.join(outside)}")
    return names, bad


def audit_outline(
    nodes: list[dict],
    principals: list[dict],
    known_terms: set[str],
    cast=None,
    *,
    canon_text: str = "",
) -> FidelityAudit:
    """Audit a generated outline against the canon's principals + known terms.

    With `cast` (an app.planning.outline.CastIndex), the structural cast fields
    count toward recall by the character they resolve to (so `character_ids`
    count, not just names in prose), and an entry naming nobody in the cast is
    its own warning.

    `canon_text` (see canon_known_text) is the canon's authored prose: names it
    already uses are never reported as inventions. Titles count toward
    principal recall but not toward the invented-names warning.
    """
    text = flatten_outline(nodes)
    structural_bad: list[str] = []
    if cast is not None:
        names, structural_bad = _structural_cast(nodes, cast)
        text = "  ".join([text, *names])
    recall, present, missing = principal_recall(text, principals)

    allow = set(known_terms)
    for p in principals:
        allow.add(p["name"])
        allow.update(p.get("aliases", []))
    _titles, body = _outline_fields(nodes)
    unknown = unknown_proper_nouns("  ".join(body), allow, canon_text=canon_text)

    warnings: list[str] = []
    if missing:
        warnings.append(
            "Outline is missing principal characters: " + ", ".join(missing)
        )
    if structural_bad:
        warnings.append(
            "Chapters cast characters who are not in the canon (dropped from "
            "their cast): " + "; ".join(structural_bad[:12])
        )
    if unknown:
        warnings.append(
            "Outline introduces names not in the canon (may be inventions): "
            + ", ".join(unknown[:12])
        )
    return FidelityAudit(recall, present, missing, unknown, warnings)
