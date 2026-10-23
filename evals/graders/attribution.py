"""Voice-attribution grader — the keystone voice metric.

Given a generated line (produced when the pipeline was asked to voice character
X) and each character's held-out gold lines, decide which character the
generated line most resembles by embedding similarity to per-character
centroids. Score = did X rank #1 (top-1 accuracy). Chance = 1 / #characters.

Pure math over embedding vectors — no network, no LLM — so it is unit-testable
with synthetic vectors and cheap to run over real embeddings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from evals.harness.vecmath import centroid, cosine


def baselines(
    reference_vecs: dict[str, list[list[float]]],
    generated: list[tuple[str, list[float]]],
) -> dict[str, float]:
    """W15-4 topic control: b_c, the mean similarity of c's (train) centroid
    to the lines generated for every OTHER character (leave-one-character-out).
    The probes share one situation, so a centroid on its topic draws every
    line up and one on a topic of its own (The Creature's cottagers) draws
    none; ranking by sim - b_c lets a voice win off its topic."""
    out = {}
    for name, vecs in reference_vecs.items():
        if not vecs:
            continue
        c = centroid(vecs)
        others = [v for who, v in generated if who != name]
        out[name] = sum(cosine(v, c) for v in others) / len(others) if others else 0.0
    return out


def rank_character(
    generated_vec: list[float],
    reference_vecs: dict[str, list[list[float]]],
    baseline: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Characters ranked by similarity of `generated_vec` to their reference
    centroid (less each one's `baseline`, when given), most similar first."""
    base = baseline or {}
    scored = [
        (name, cosine(generated_vec, centroid(vecs)) - base.get(name, 0.0))
        for name, vecs in reference_vecs.items()
        if vecs
    ]
    return sorted(scored, key=lambda t: t[1], reverse=True)


@dataclass
class AttributionResult:
    intended: str
    predicted: str
    correct: bool
    margin: float  # top score minus the intended character's score (0 if correct)
    ranking: list[tuple[str, float]]

    @property
    def gap(self) -> float:
        """Top-1 minus top-2 score: how near the next voice is (SPEC-33)."""
        return self.ranking[0][1] - self.ranking[1][1] if len(self.ranking) > 1 else 0.0

    def as_dict(self) -> dict:
        return {
            "intended": self.intended,
            "predicted": self.predicted,
            "correct": self.correct,
            "margin": round(self.margin, 4),
            "gap": round(self.gap, 4),
            "ranking": [(n, round(s, 4)) for n, s in self.ranking],
        }


def attribute(
    intended: str,
    generated_vec: list[float],
    reference_vecs: dict[str, list[list[float]]],
    baseline: dict[str, float] | None = None,
) -> AttributionResult:
    ranking = rank_character(generated_vec, reference_vecs, baseline)
    predicted = ranking[0][0] if ranking else ""
    scores = dict(ranking)
    margin = (ranking[0][1] - scores.get(intended, 0.0)) if ranking else 0.0
    return AttributionResult(
        intended=intended,
        predicted=predicted,
        correct=(predicted == intended),
        margin=margin,
        ranking=ranking,
    )


def accuracy(results: list[AttributionResult]) -> dict:
    """Aggregate: top-1 accuracy, chance baseline and the mean gap."""
    n = len(results)
    correct = sum(r.correct for r in results)
    n_chars = max((len(r.ranking) for r in results), default=0)
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "chance": round(1 / n_chars, 4) if n_chars else 0.0,
        "mean_gap": round(sum(r.gap for r in results) / n, 4) if n else None,
    }


MASK = "⟨NAME⟩"
_HONORIFIC = r"(?:(?:Mr|Mrs|Ms|Dr)\.\s+|Miss\s+)?"
# W10-5 (D8): a name's first token that is also a word is masked only within
# the whole name ("Old Fen"), never alone ("Old habits"). Grader-local.
_WORD_FIRST = {"Old", "The", "Mr", "Mrs", "Ms", "Dr", "Miss"}


def mask(text: str, forms: dict[str, list[str]]) -> str:
    """W9-2 (b): each cast form ({name: [name, *aliases]}) and each name's
    first and last token, whole-word and case-sensitive with an optional
    honorific, replaced by MASK, so a vocative is no speaker label. An alias
    is masked whole, never token by token ("Chief Executive Lich")."""
    tokens = (t for n in forms for t in (n.split()[0], n.split()[-1]))
    words = {*(f for fs in forms.values() for f in fs), *(set(tokens) - _WORD_FIRST)}
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return (
        text
        if not alts
        else re.sub(rf"(?<!\w){_HONORIFIC}(?:{alts})(?!\w)", MASK, text)
    )
