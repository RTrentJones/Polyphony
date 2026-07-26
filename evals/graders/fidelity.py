"""Outline-fidelity grader — the "Elara detector".

The incident this exists for: a 20k-char synopsis about Milo Voss and Zara Okafor
produced a 12-chapter outline starring an invented protagonist "Elara", because
the model received 2,000 of 20,001 chars and an empty cast (docs/BRD.md §1). The
judge scored *structure*, not *fidelity to the source's people*, so it stayed
green. This grader measures the thing that actually failed.

Two signals (docs/BRD.md R1.4–R1.6):

* **principal_recall — the HARD gate.** Every principal in the canon
  (protagonist / antagonist / main) must appear in the outline. Exact, word-
  boundary, alias-aware match on names — *no NLP, no false positives.* "Elara"
  was a recall failure (the real leads were absent) as much as a precision one,
  and recall is the reliable, cheap signal. On the Elara outline this is ~0.

* **unknown_rate — a SOFT warning.** Proper nouns in the outline that aren't in
  the canon. A good outline legitimately invents an innkeeper, so this only warns
  — never hard-fails an expensive job on a heuristic.

`score = principal_recall * (1 - unknown_rate)`: a beautiful outline about the
wrong story scores ~0; a faithful one that invents a little scores near 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extraction import _norm

# Words that start sentences / fill headings and get capitalized without being
# proper nouns — kept out of the unknown-noun scan to cut false positives.
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

# Capitalized word run (proper-noun candidate) or an all-caps acronym (CEL).
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[A-Z]{2,}\b")


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


def _present(needle: str, haystack_norm: str) -> bool:
    """Word-boundary, normalized presence of one name/alias in the outline."""
    n = _norm(needle)
    if not n:
        return False
    return re.search(rf"(?<!\w){re.escape(n)}(?!\w)", haystack_norm) is not None


def principal_recall(
    outline_text: str, principals: list[dict]
) -> tuple[float, list[str], list[str]]:
    """Fraction of canon principals present (by name OR any alias).

    `principals`: [{"name": str, "aliases": [str, ...]}]. Returns
    (recall, present_names, missing_names).
    """
    if not principals:
        return 1.0, [], []
    hay = _norm(outline_text)
    present, missing = [], []
    for p in principals:
        forms = [p["name"], *p.get("aliases", [])]
        (present if any(_present(f, hay) for f in forms) else missing).append(p["name"])
    return round(len(present) / len(principals), 4), present, missing


def unknown_rate(outline_text: str, known: set[str]) -> tuple[float, list[str]]:
    """Fraction of the outline's proper nouns that are not in the canon.

    Soft signal — a good outline may invent a minor name. Returns
    (rate, sorted_unknown_surface_forms).
    """
    known_norm = {_norm(k) for k in known}
    found: dict[str, str] = {}  # norm -> surface form (first seen)
    for m in _PROPER.finditer(outline_text):
        surface = m.group(0)
        norm = _norm(surface)
        if not norm or norm in _STOP_CAPS:
            continue
        found.setdefault(norm, surface)
    if not found:
        return 0.0, []
    unknown = {
        norm: surface
        for norm, surface in found.items()
        # known if it matches, or is a token-subset of, a canon entity
        if not any(
            norm == k or norm in k.split() or k in norm.split() for k in known_norm
        )
    }
    rate = round(len(unknown) / len(found), 4)
    return rate, sorted(unknown.values())


@dataclass
class FidelityScore:
    principal_recall: float
    unknown_rate: float
    score: float
    present: list[str]
    missing: list[str]
    unknown: list[str]

    def as_dict(self) -> dict:
        return {
            "principal_recall": self.principal_recall,
            "unknown_rate": self.unknown_rate,
            "score": self.score,
            "present": self.present,
            "missing": self.missing,
            "unknown": self.unknown,
        }


def grade_fidelity(
    nodes: list[dict], principals: list[dict], known: set[str]
) -> FidelityScore:
    """Grade an outline for fidelity to the canon's people.

    `known` is the full proper-noun allowlist (cast + aliases + places/orgs);
    principal names and aliases are folded in automatically.
    """
    text = flatten_outline(nodes)
    recall, present, missing = principal_recall(text, principals)

    allow = set(known)
    for p in principals:
        allow.add(p["name"])
        allow.update(p.get("aliases", []))
    urate, unknown = unknown_rate(text, allow)

    score = round(recall * (1 - urate), 4)
    return FidelityScore(recall, urate, score, present, missing, unknown)
