"""Tiny pure-Python vector helpers shared by the graders and steps.

No numpy dependency — the vectors are small (384-dim) and this keeps the eval
package importable without pulling scientific stack into the runtime image.
"""

from __future__ import annotations

Vector = list[float]


def cosine(a: Vector, b: Vector) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


def centroid(vectors: list[Vector]) -> Vector:
    if not vectors:
        return []
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]
