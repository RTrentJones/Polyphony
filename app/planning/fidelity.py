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
from typing import Optional

from app.parsing.names import (
    ABBREVIATED_HONORIFICS,
    HONORIFICS,
    NON_NAME_TOKENS,
    mentions,
    norm,
)
from app.planning.outline import classify_people, people_refs

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
    "our",
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
FUNCTION_WORDS = _STOP_CAPS | _CONNECTIVES | _CALENDAR
# Capitalised runs joined by a SINGLE space: a run never spans a field boundary
# (fields are joined with two spaces), a comma or a line break, so "Debt" ending
# one title and "In" opening the next summary are two words, not "Debt  In".
# An all-caps token (a sign or a stamp: INITIAL HERE) is never a candidate
# (SPEC-33, N10). An abbreviated honorific with its period joins the run
# ("Mr. Quince"); a whole-word title ("the Count.") never joins across a period.
_HONORIFIC = "|".join(sorted(h.capitalize() for h in ABBREVIATED_HONORIFICS))
_TOKEN = rf"(?:(?:{_HONORIFIC})\.[ ])?[A-Z][a-z]+"
_PROPER = re.compile(rf"\b{_TOKEN}(?:[ ]{_TOKEN})*\b")
_WORD = re.compile(r"[A-Za-z]+")
_OPENERS = '.!?;:"“(—'


def canon_known_text(canon) -> str:
    """The canon's authored text (synopsis + cast + entries + style): every
    capitalised phrase in it is a known name, not an outline invention."""
    parts = [getattr(canon, "synopsis", "") or ""]
    bible = getattr(canon, "bible", None)
    if callable(bible):
        parts.append(bible() or "")
    return "\n".join(p for p in parts if p)


def flatten_outline(nodes: list[dict]) -> tuple[list[str], list[str]]:
    """(titles, everything else) an outline shows, in one walk: summaries,
    pov, premise, the `people` lists (and the legacy `characters` /
    `secondary_names`) and threads. A declared person is checked too: one the
    canon never names is an invention, and the warning says so (SPEC-24)."""
    titles: list[str] = []
    body: list[str] = []

    def walk(node: dict) -> None:
        for key in ("title", "summary", "pov", "premise_restated", "central_conflict"):
            val = node.get(key)
            if isinstance(val, str):
                (titles if key == "title" else body).append(val)
        for key in ("people", "characters", "secondary_names", "threads"):
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


def principal_recall(
    outline_text: str, principals: list[dict]
) -> tuple[float, list[str], list[str]]:
    """Fraction of canon principals present by name OR any alias."""
    if not principals:
        return 1.0, [], []
    hay = norm(outline_text)
    present, missing = [], []
    for p in principals:
        forms = [p["name"], *p.get("aliases", [])]
        (present if any(mentions(hay, f) for f in forms) else missing).append(p["name"])
    return round(len(present) / len(principals), 4), present, missing


def _function_word(word: str) -> bool:
    """A function or calendar word; a calendar word also as a plural ("Fridays")."""
    w = word.lower()
    return w in FUNCTION_WORDS or (w.endswith("s") and w[:-1] in _CALENDAR)


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


# A run followed by a single capital, a number or a code is a label
# ("Appendix F", "Form 9", "Desk R-14"), not a name (M5).
_LABEL_TOKEN = re.compile(r"\s+(?:[A-Z](?![a-z])|\d|[A-Z]+-?\d)")
# A run cut by a contraction's apostrophe ("'Don't explain.'") is not a name;
# a possessive is ("Gary's desk"), so 's never ends one (SPEC-57).
_CONTRACTION = re.compile(r"['’](?:t|ll|re|ve|d)\b")


def _candidates(text: str):
    """Yield (words, sentence_initial) for each capitalised run in `text`, with
    a sentence's leading and trailing function words ("The", "Under", "At")
    trimmed. A label is not a run, nor is a contraction's stem. Mid-sentence,
    a function word before a single word is kept ("Our Values" reads as a title
    fragment), so a one-word name after a mid-sentence "The" is not reported."""
    for m in _PROPER.finditer(text or ""):
        if _LABEL_TOKEN.match(text, m.end()) or _CONTRACTION.match(text, m.end()):
            continue
        words = m.group(0).split(" ")
        initial = opens = _is_sentence_initial(text, m.start())
        # Mid-sentence, a function word before ONE word opens a title ("a
        # slide called Our Values"): it stays, so the run reads as a fragment.
        while words and _function_word(words[0]) and (opens or len(words) > 2):
            words.pop(0)
            initial = False  # what is left no longer opens the sentence
        while words and _function_word(words[-1]):
            words.pop()
        if words:
            yield words, initial


def proper_nouns(text: str) -> set[str]:
    """The distinct capitalised phrases in `text` (normalised): the
    denominator of the evals' unknown-name rate."""
    return {norm(" ".join(words)) for words, _ in _candidates(text)} - {""}


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
    - it is one word that only ever opens a sentence or field, never seen
      mid-sentence in the text or the canon ("Silence fell.", "Nobody came").
    `audit_outline` passes outline text WITHOUT chapter/beat titles, so a
    phrase that only ever appears inside a title is skipped too.
    """
    known_norm = {norm(k) for k in known if norm(k)}
    for words, _initial in _candidates(canon_text):
        known_norm.add(norm(" ".join(words)))
    known_tokens = {tok for k in known_norm for tok in k.split()}
    lower_words = {
        w for w in _WORD.findall(f"{outline_text}\n{canon_text}") if w.islower()
    }
    mid = {
        norm(" ".join(words))
        for text in (outline_text, canon_text)
        for words, initial in _candidates(text)
        if not initial
    }

    found: dict[str, str] = {}
    for words, initial in _candidates(outline_text):
        lowered = [w.lower() for w in words]
        if any(_function_word(w) for w in words):
            continue  # title-case fragment
        if all(w in lower_words for w in lowered):
            continue  # common words, capitalised
        key = norm(" ".join(words))
        if initial and len(words) == 1 and key not in mid:
            continue  # only ever opens a sentence ("Silence fell.")
        if set(key.split()) - HONORIFICS - NON_NAME_TOKENS:  # SPEC-44
            found.setdefault(key, " ".join(words))
    unknown: list[str] = []
    for key, surface in found.items():
        name = " ".join(t for t in key.split() if t not in HONORIFICS)
        if all(tok in known_tokens for tok in name.split()) or any(
            name == k or name in k.split() or k in name.split() for k in known_norm
        ):
            continue
        # Two reported runs joined by " and " / " of " are one name (SPEC-44).
        both = unknown and re.search(
            rf"{re.escape(unknown[-1])} (?:and|of) {re.escape(surface)}\b",
            outline_text,
        )
        if both:
            unknown[-1] = both.group(0)
        else:
            unknown.append(surface)
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


def _structural_cast(
    nodes: list[dict], cast, known_text: Optional[str]
) -> tuple[list[str], list[str]]:
    """(names the top-level people resolve to, per-chapter entries that are
    neither cast nor a person the canon names). Each chapter's `people` (and
    legacy `character_ids` / `characters` / `secondary_names`) go through one
    classify_people call, the rule validation applies (R1.5, SPEC-24): its
    `invented` entries, plus a `pov` that does not resolve, are reported per
    chapter, never left to the advisory proper-noun heuristic."""
    names: list[str] = []
    bad: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        people = classify_people(people_refs(node), cast, known_text)
        outside = list(people.invented)
        pov = node.get("pov")
        if isinstance(pov, str) and pov.strip():
            pid = cast.resolve(pov)
            if pid is None:
                outside.append(pov)
            elif pid not in people.ids:
                people.ids.append(pid)
        names.extend(cast.names[cid] for cid in people.ids)
        if outside:
            bad.append(f"{node.get('title', '?')}: {', '.join(outside)}")
    return names, bad


# D9 (SPEC-49): a scene summary that says what its POV character cannot witness.
_POV_LEAK = re.compile(
    r"\b(does not see|does not notice|the reader|will later|would later|cannot see)\b",
    re.IGNORECASE,
)
POV_LEAK_WARNING = "Scene summaries say what their POV character cannot witness (edit them in the beat editor): "


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
    titles, body = flatten_outline(nodes)
    text = "  ".join([*titles, *body])
    structural_bad: list[str] = []
    if cast is not None:
        names, structural_bad = _structural_cast(nodes, cast, norm(canon_text))
        text = "  ".join([text, *names])
    recall, present, missing = principal_recall(text, principals)

    allow = set(known_terms)
    for p in principals:
        allow.add(p["name"])
        allow.update(p.get("aliases", []))
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
    leaks = [  # chapters numbered from 1 in outline order
        f'chapter {i} "{scene.get("title", "")}" ("{m.group(1).lower()}")'
        for i, chapter in enumerate((n for n in nodes if isinstance(n, dict)), 1)
        for scene in chapter.get("children") or []
        if isinstance(scene, dict)
        and (m := _POV_LEAK.search(str(scene.get("summary") or "")))
    ]
    if leaks:
        warnings.append(POV_LEAK_WARNING + "; ".join(leaks))
    return FidelityAudit(recall, present, missing, unknown, warnings)
