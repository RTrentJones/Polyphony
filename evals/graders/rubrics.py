"""The R1–R6 judge rubrics (cycle 2026-10 PM backlog §3), verbatim, plus the
arithmetic that folds per-rubric scores into an artifact score.

The judge is shown one rubric at a time with the book's canon, the style
guide, the plan (beats) and the text, and answers {"score", "explanation"}.
Deterministic pre-checks (cast recall, seam overlap, quote-verbatim) run
first: a deterministic failure caps its rubric at 0 whatever the judge says
(`apply_caps`), because the judge never overrides a measured failure.

    outline score = R1 x mean(R6, R3)
    scene score   = R1 x mean(R2, R3, R4, R5)
"""

from __future__ import annotations

import statistics

RUBRICS: dict[str, str] = {
    "R1": (
        "### R1. Cast fidelity (outlines and scenes)\n"
        "Are the people on the page the book's people, behaving as the canon says?\n"
        "- **1.0**: Every principal named in the canon appears where the plan says. Names and aliases\n"
        '  are exact, and in-world terms are read correctly ("the CEL" = Chief Executive Lich). Invented\n'
        "  people are minor and functional (an innkeeper). Nobody acts against their canon description.\n"
        "- **0.7**: All principals are present. One character acts slightly off-description, or one\n"
        "  invented secondary character takes a noticeable role.\n"
        "- **0.4**: A principal is missing or reduced to a mention, or an in-world term is misread.\n"
        '- **0.0**: An invented protagonist (the "Elara" case), or the cast is largely not the canon\'s.'
    ),
    "R2": (
        "### R2. Voice distinctness and fidelity (scenes)\n"
        "With speaker tags removed, can each character be told apart, and do they sound like their samples?\n"
        "- **1.0**: The judge attributes ≥ 90% of untagged lines correctly. Each voice matches its samples'\n"
        "  diction, cadence and syntax. No two characters share verbal tics.\n"
        "- **0.7**: 70–89% attributable. Voices differ but one drifts toward generic or narrator voice.\n"
        "- **0.4**: 50–69% attributable. Characters differ mostly in content, not in how they speak.\n"
        "- **0.0**: Interchangeable voices, or a character speaks in a way their samples contradict."
    ),
    "R3": (
        "### R3. Beat adherence (scenes, and outline beats against the chapter)\n"
        "Does the text do what the plan says, in order, and no more?\n"
        "- **1.0**: Every planned beat is realised in order. Each beat's stated change actually happens. No\n"
        "  later beat is anticipated, and nothing major is added that the plan doesn't contain.\n"
        "- **0.7**: All beats are present, with one out of order or one pre-empted early.\n"
        "- **0.4**: A beat is missing or its change doesn't land, or there are significant unplanned events.\n"
        "- **0.0**: The text ignores the plan."
    ),
    "R4": (
        "### R4. Style-guide adherence and prose quality (scenes)\n"
        "Does the prose follow the book's POV, tense, tone and sample prose, and is it publishable-draft\n"
        "quality?\n"
        "- **1.0**: POV and tense are consistent in ≥ 95% of sentences. Tone and rhythm resemble the sample\n"
        "  prose. Sentences are concrete and varied. No repeated sentences, no beat labels, no\n"
        '  "Name:" prefixes, no summary-instead-of-scene.\n'
        "- **0.7**: Style is right, with occasional slips (1–3 sentences), some generic phrasing or mild\n"
        "  repetition.\n"
        "- **0.4**: Noticeable POV or tense drift, a tone mismatch with the sample, or visible stitching\n"
        "  between beats.\n"
        "- **0.0**: Wrong POV or tense throughout, or the output is not narrative prose (a list, a script,\n"
        "  headings)."
    ),
    "R5": (
        "### R5. Continuity (scenes against canon and prior text; continuity reports against planted errors)\n"
        "For generated prose: does it contradict anything established? For a continuity report: did it find\n"
        "the planted contradictions and only those?\n"
        "- **1.0**: Prose contradicts nothing in the canon, plot state or prior scenes (names, physical\n"
        "  facts, objects, timeline, who knows what). A report finds all planted contradictions, with\n"
        "  verbatim quotes and no false positives.\n"
        "- **0.7**: Prose has one minor contradiction. A report finds ≥ 80% of planted errors with ≤ 1\n"
        "  false positive.\n"
        "- **0.4**: Prose has one major contradiction (a dead character speaks, an object teleports). A\n"
        "  report finds 50–79%, or has several false positives.\n"
        "- **0.0**: Prose has multiple major contradictions. A report misses most planted errors, or reports\n"
        '  "clean" on unchecked text.'
    ),
    "R6": (
        "### R6. Outline structure and premise alignment (outlines)\n"
        "Does the outline realise *this* book's premise as a working structure?\n"
        "- **1.0**: The outline restates the confirmed premise faithfully. Each act's turn is realised by\n"
        "  its final chapters. Every chapter summary states what *changes*. The central conflict escalates\n"
        "  to a climax. Chapter count is within ±25% of the request.\n"
        "- **0.7**: The structure is sound, but one act turn is weak, or 1–2 chapters are static (events\n"
        "  without change).\n"
        "- **0.4**: Generic structure, loosely tied to the premise, or several static chapters.\n"
        "- **0.0**: A well-shaped outline for a different book, or no discernible arc."
    ),
}

# Which rubrics grade which artifact.
SCENE_RUBRICS = ("R1", "R2", "R3", "R4", "R5")
OUTLINE_RUBRICS = ("R1", "R6", "R3")

_INSTRUCTIONS = (
    "Score this artifact against the rubric above on 0.0-1.0. The anchors are "
    "fixed points: interpolate between them, and grade strictly (a draft that "
    "only nearly meets an anchor scores below it). Judge only what the rubric "
    "asks; the canon, style guide and plan given with the artifact are the "
    "ground truth to judge it against."
)


def rubric(rid: str) -> str:
    """The rubric text the judge receives for `rid` (R1..R6)."""
    if rid not in RUBRICS:
        raise KeyError(f"unknown rubric {rid!r} (known: {sorted(RUBRICS)})")
    return f"{RUBRICS[rid]}\n\n{_INSTRUCTIONS}"


def apply_caps(
    scores: dict[str, float | None], failures: dict[str, str]
) -> dict[str, float | None]:
    """Scores with every rubric that failed a deterministic pre-check set to 0.

    `failures` maps rubric id -> why (e.g. {"R1": "principal missing"}). A
    capped rubric is 0 even when the judge was unavailable (None): the failure
    is measured, not a judgment."""
    out = dict(scores)
    for rid in failures:
        out[rid] = 0.0
    return out


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(vals) if vals else None


def scene_score(scores: dict[str, float | None]) -> float | None:
    """R1 x mean(R2..R5); None when R1 or every other rubric is unscored."""
    r1, rest = scores.get("R1"), _mean([scores.get(r) for r in SCENE_RUBRICS[1:]])
    if r1 is None or rest is None:
        return None
    return round(r1 * rest, 4)


def outline_score(scores: dict[str, float | None]) -> float | None:
    """R1 x mean(R6, R3); None when R1 or both others are unscored."""
    r1, rest = scores.get("R1"), _mean([scores.get("R6"), scores.get("R3")])
    if r1 is None or rest is None:
        return None
    return round(r1 * rest, 4)
