"""Voice-attribution grader — the keystone voice metric.

Given a generated line (produced when the pipeline was asked to voice character
X) and each character's held-out gold lines, decide which character the
generated line most resembles by embedding similarity to per-character
centroids. Score = did X rank #1 (top-1 accuracy). Chance = 1 / #characters.

Pure math over embedding vectors — no network, no LLM — so it is unit-testable
with synthetic vectors and cheap to run over real embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]


def rank_character(
    generated_vec: list[float], reference_vecs: dict[str, list[list[float]]]
) -> list[tuple[str, float]]:
    """Characters ranked by similarity of `generated_vec` to their reference
    centroid, most similar first."""
    scored = [
        (name, _cos(generated_vec, _centroid(vecs)))
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

    def as_dict(self) -> dict:
        return {
            "intended": self.intended,
            "predicted": self.predicted,
            "correct": self.correct,
            "margin": round(self.margin, 4),
            "ranking": [(n, round(s, 4)) for n, s in self.ranking],
        }


def attribute(
    intended: str,
    generated_vec: list[float],
    reference_vecs: dict[str, list[list[float]]],
) -> AttributionResult:
    ranking = rank_character(generated_vec, reference_vecs)
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
    """Aggregate: top-1 accuracy and chance baseline."""
    n = len(results)
    correct = sum(r.correct for r in results)
    n_chars = max((len(r.ranking) for r in results), default=0)
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "chance": round(1 / n_chars, 4) if n_chars else 0.0,
    }
