"""Character-extraction grader: precision / recall / F1 vs the gold cast.

Names are matched leniently (case-insensitive, alias-resolved to the gold form)
so "Prof. Verhoeven" and "Verhoeven" count as one hit, and a first-or-last-name
match against a multi-word gold name still counts.
"""

from __future__ import annotations

from dataclasses import dataclass


def _norm(name: str) -> str:
    return " ".join(name.lower().replace(".", " ").split())


def _tokens(name: str) -> set[str]:
    # drop honorifics so "Prof. Verhoeven" ~ "Verhoeven"
    drop = {"prof", "professor", "dr", "mr", "mrs", "miss", "count", "lord", "sir"}
    return {t for t in _norm(name).split() if t not in drop}


def match(extracted: str, gold: str) -> bool:
    """A predicted name matches a gold name if their significant tokens overlap
    (covers 'Verhoeven' vs 'Prof. Verhoeven', 'Nora' vs 'Nora Vance')."""
    e, g = _tokens(extracted), _tokens(gold)
    return bool(e & g)


@dataclass
class ExtractionScore:
    precision: float
    recall: float
    f1: float
    true_positives: list[str]
    missed: list[str]
    spurious: list[str]

    def as_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "true_positives": self.true_positives,
            "missed": self.missed,
            "spurious": self.spurious,
        }


def grade_extraction(predicted: list[str], gold: list[str]) -> ExtractionScore:
    """Score a predicted cast against the gold cast.

    Each gold name is hit at most once; each prediction is spurious if it matches
    no gold name. Precision = hits / predictions, recall = hits / gold.
    """
    gold_hit: dict[str, bool] = {g: False for g in gold}
    spurious: list[str] = []
    for p in predicted:
        matched = next((g for g in gold if not gold_hit[g] and match(p, g)), None)
        if matched is None:
            # allow matching an already-hit gold (duplicate prediction) w/o double count
            if not any(match(p, g) for g in gold):
                spurious.append(p)
        else:
            gold_hit[matched] = True

    tp = [g for g, hit in gold_hit.items() if hit]
    missed = [g for g, hit in gold_hit.items() if not hit]
    n_pred = len(predicted)
    precision = len(tp) / n_pred if n_pred else 0.0
    recall = len(tp) / len(gold) if gold else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    )
    return ExtractionScore(precision, recall, f1, tp, missed, spurious)
