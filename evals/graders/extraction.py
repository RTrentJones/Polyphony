"""Character-extraction grader: precision / recall / F1 vs the gold cast.

Names are matched leniently (case-insensitive, alias-resolved to the gold form)
so "Prof. Verhoeven" and "Verhoeven" count as one hit, and a first-or-last-name
match against a multi-word gold name still counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.parsing.names import match  # the shared rule (SPEC-09)


@dataclass
class ExtractionScore:
    precision: float
    recall: float
    f1: float
    true_positives: list[str]
    missed: list[str]
    spurious: list[str]
    secondary_found: list[str]

    def as_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "true_positives": self.true_positives,
            "missed": self.missed,
            "spurious": self.spurious,
            "secondary_found": self.secondary_found,
        }


def grade_extraction(
    predicted: list[str], gold: list[str], secondary: list[str] = ()
) -> ExtractionScore:
    """Score a predicted cast against the gold cast and the corpus' real
    `secondary` people (SPEC-33). Each name is hit at most once; a prediction
    matching neither is spurious. Precision = (gold + secondary hits) /
    predictions, recall = gold hits / gold.
    """
    gold_hit: dict[str, bool] = {g: False for g in [*gold, *secondary]}
    spurious: list[str] = []
    for p in predicted:
        matched = next((g for g in gold_hit if not gold_hit[g] and match(p, g)), None)
        if matched is None:
            # allow matching an already-hit gold (duplicate prediction) w/o double count
            if not any(match(p, g) for g in gold_hit):
                spurious.append(p)
        else:
            gold_hit[matched] = True

    tp = [g for g in gold if gold_hit[g]]
    missed = [g for g in gold if not gold_hit[g]]
    found = sorted(s for s in secondary if gold_hit[s])
    n_pred = len(predicted)
    precision = (len(tp) + len(found)) / n_pred if n_pred else 0.0
    recall = len(tp) / len(gold) if gold else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    )
    return ExtractionScore(precision, recall, f1, tp, missed, spurious, found)
