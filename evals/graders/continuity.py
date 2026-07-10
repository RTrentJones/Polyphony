"""Continuity grader with constructed ground truth — the cleanest eval.

We take clean renamed text and inject KNOWN contradictions (a respelled name, a
date flipped out of sequence, …). The continuity checker runs over the injected
text; because we authored the errors we know exactly what should be flagged.

- detection recall  = injected contradictions that produced a matching finding
- false-positive rate = findings raised on the CLEAN control (should be ~0)

`apply_injections` returns the mutated text + the expected findings; `grade`
scores a checker's actual findings against them.
"""

from __future__ import annotations

from dataclasses import dataclass


def apply_injections(clean_text: str, injections: list[dict]) -> tuple[str, list[dict]]:
    """Return (mutated_text, expected) where expected is the list of planted
    contradictions the checker should catch. Each injection replaces the FIRST
    occurrence of `locate` with `replace`, leaving other occurrences intact so a
    real contradiction (old vs new) exists in the text."""
    text = clean_text
    expected = []
    for inj in injections:
        loc, rep = inj["locate"], inj["replace"]
        idx = text.find(loc)
        if idx == -1:
            continue
        text = text[:idx] + rep + text[idx + len(loc) :]
        expected.append(
            {
                "id": inj["id"],
                "expect_type": inj.get("expect_type"),
                "replace": rep,
                "original": loc,
            }
        )
    return text, expected


def _finding_matches(expected: dict, finding: dict) -> bool:
    """A finding catches an injection if it references either the mutated token
    or the original, in its detail/refs text (type-agnostic — checkers vary in
    how they label, so we match on the concrete contradiction)."""
    hay = f"{finding.get('detail', '')} {finding.get('refs', '')}".lower()
    return expected["replace"].lower() in hay or expected["original"].lower() in hay


@dataclass
class ContinuityScore:
    detection_recall: float
    false_positive_rate: float
    detected: list[str]
    missed: list[str]
    n_injected: int
    n_control_findings: int

    def as_dict(self) -> dict:
        return {
            "detection_recall": round(self.detection_recall, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "detected": self.detected,
            "missed": self.missed,
            "n_injected": self.n_injected,
            "n_control_findings": self.n_control_findings,
        }


def grade_continuity(
    expected: list[dict],
    injected_findings: list[dict],
    control_findings: list[dict],
) -> ContinuityScore:
    """
    expected:          from apply_injections.
    injected_findings: the checker's findings on the injected text.
    control_findings:  the checker's findings on the CLEAN text (false positives).
    """
    detected, missed = [], []
    for exp in expected:
        if any(_finding_matches(exp, f) for f in injected_findings):
            detected.append(exp["id"])
        else:
            missed.append(exp["id"])
    n = len(expected)
    # FPR: control findings normalized by control size (a rate in [0, 1+]); a
    # clean text should yield ~0. Cap at 1.0 for reporting sanity.
    fpr = min(1.0, len(control_findings) / max(1, n))
    return ContinuityScore(
        detection_recall=len(detected) / n if n else 0.0,
        false_positive_rate=round(fpr, 4),
        detected=detected,
        missed=missed,
        n_injected=n,
        n_control_findings=len(control_findings),
    )
