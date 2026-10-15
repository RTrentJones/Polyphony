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

Both run the app's own rules (app.planning.fidelity, W-4): the grader measures
what the product checks, with no second normaliser or outline walk.

`score = principal_recall * (1 - unknown_rate)`: a beautiful outline about the
wrong story scores ~0; a faithful one that invents a little scores near 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.planning.fidelity import (
    flatten_outline,
    principal_recall,
    proper_nouns,
    unknown_proper_nouns,
)


def unknown_rate(outline_text: str, known: set[str]) -> tuple[float, list[str]]:
    """Fraction of the outline's proper nouns that are not in the canon, by
    the outline audit's rule (`unknown_proper_nouns`).

    Soft signal — a good outline may invent a minor name. Returns
    (rate, sorted_unknown_surface_forms).
    """
    found = proper_nouns(outline_text)
    if not found:
        return 0.0, []
    unknown = unknown_proper_nouns(outline_text, known)
    return round(len(unknown) / len(found), 4), unknown


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
    titles, body = flatten_outline(nodes)
    recall, present, missing = principal_recall("  ".join([*titles, *body]), principals)

    allow = set(known)
    for p in principals:
        allow.add(p["name"])
        allow.update(p.get("aliases", []))
    # Titles count toward recall, not invention (as the app audit).
    urate, unknown = unknown_rate("  ".join(body), allow)

    score = round(recall * (1 - urate), 4)
    return FidelityScore(recall, urate, score, present, missing, unknown)
