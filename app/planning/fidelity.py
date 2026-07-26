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
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[A-Z]{2,}\b")


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").split())


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


def unknown_proper_nouns(outline_text: str, known: set[str]) -> list[str]:
    known_norm = {_norm(k) for k in known}
    found: dict[str, str] = {}
    for m in _PROPER.finditer(outline_text):
        surface = m.group(0)
        norm = _norm(surface)
        if not norm or norm in _STOP_CAPS:
            continue
        found.setdefault(norm, surface)
    unknown = [
        surface
        for norm, surface in found.items()
        if not any(
            norm == k or norm in k.split() or k in norm.split() for k in known_norm
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


def audit_outline(
    nodes: list[dict], principals: list[dict], known_terms: set[str]
) -> FidelityAudit:
    """Audit a generated outline against the canon's principals + known terms."""
    text = flatten_outline(nodes)
    recall, present, missing = principal_recall(text, principals)

    allow = set(known_terms)
    for p in principals:
        allow.add(p["name"])
        allow.update(p.get("aliases", []))
    unknown = unknown_proper_nouns(text, allow)

    warnings: list[str] = []
    if missing:
        warnings.append(
            "Outline is missing principal characters: " + ", ".join(missing)
        )
    if unknown:
        warnings.append(
            "Outline introduces names not in the canon (may be inventions): "
            + ", ".join(unknown[:12])
        )
    return FidelityAudit(recall, present, missing, unknown, warnings)
