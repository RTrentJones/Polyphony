"""The R1–R6 judge rubrics (cycle 2026-10 PM backlog §3), verbatim, the RA
revise rubric (SPEC-28), plus the arithmetic that folds per-rubric scores into
an artifact score.

The judge is shown one rubric at a time with the book's canon, the style
guide, the plan (beats) and the text, and answers {"score", "explanation"}.
Deterministic pre-checks (cast recall, seam overlap, quote-verbatim) run
first: a deterministic failure caps its rubric at 0 whatever the judge says
(`apply_caps`), because the judge never overrides a measured failure.

    outline score = R1 x mean(R6, R3)
    scene score   = R1 x mean(R2, R3, R4, R5)
    revise score  = mean(RA, R5)

RN (narrative voice, per scene) and RC (the chapter as a whole) are the
`book` rubrics (SPEC-93 W18-6/7): reported beside the prose score, never
folded into it (D18-4), so the prose score's history stays comparable.

The scene R1 cap (SPEC-28, H1) fires only when a principal the scene's plan
names is neither named nor the attributed speaker of a quoted line, or when
none of the cast is on the page; a principal who speaks unnamed is a recorded
warning and the judge's R1 stands (evals/steps/pipeline.py).
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
    "RA": (
        "### RA. Revision instruction adherence (a revised passage)\n"
        "Given the author's instruction and the passage before and after, did the revision do what was\n"
        "asked, and only that?\n"
        "- **1.0**: The instruction is carried out fully (tightened, colder, redone) and nothing else about\n"
        "  the passage changes: same events, same people, same facts, same POV and tense.\n"
        "- **0.7**: The instruction is carried out, with one unrequested change, or only partly.\n"
        "- **0.4**: The instruction is barely visible, or the passage drifts (new events, a new person).\n"
        "- **0.0**: The instruction is ignored or contradicted, or the passage is a different scene."
    ),
    "RN": (
        "### RN. Narrative voice (scenes, dialogue removed)\n"
        "With the dialogue removed, does the scene's narration sound like this book's narrator, as the\n"
        "NARRATOR SAMPLES (the source's own narration) show it?\n"
        "- **1.0**: Same POV person and tense as the samples throughout. Register, diction and period\n"
        "  match (no modern idiom in a period narrator, no ornament in a plain one). Sentence rhythm and\n"
        "  length resemble the samples. A reader could not tell the narration from the source's.\n"
        "- **0.7**: POV and tense match; register and period are right, with a few generic or\n"
        "  out-of-period phrases, or a rhythm noticeably flatter or shorter than the samples'.\n"
        "- **0.4**: POV or tense differs from the samples in places, or the narration reads as a\n"
        "  generic modern narrator with the book's names (wrong register, diction or period).\n"
        "- **0.0**: A different POV person or tense throughout, or the narration bears no resemblance\n"
        "  to the samples (summary, a list, a script)."
    ),
    "RC": (
        "### RC. The chapter as a whole (a generated chapter against its plan)\n"
        "Read the whole CHAPTER (its scenes in order): does it read as one chapter of this book, doing\n"
        "what the PLAN says the chapter changes?\n"
        "- **1.0**: Scenes join with real transitions (time, place and who is present are clear). Pacing\n"
        "  varies with purpose. No event, description or phrasing is repeated across scenes, and no\n"
        "  scene restarts what an earlier one finished. One consistent narrator (POV, tense, register)\n"
        "  from first scene to last. The planned change lands by the chapter's end.\n"
        "- **0.7**: Reads as one chapter, with one abrupt seam, one repeated beat or phrase, or a\n"
        "  sagging stretch; the planned change lands.\n"
        "- **0.4**: Reads as separate scenes stitched together: several abrupt seams, content or\n"
        "  phrasing repeated across scenes, the narrator shifting between scenes, or the planned change\n"
        "  only half lands.\n"
        "- **0.0**: Not one chapter: scenes contradict or restart each other, or the planned change\n"
        "  never happens."
    ),
}

# Which rubrics grade which artifact.
SCENE_RUBRICS = ("R1", "R2", "R3", "R4", "R5")
OUTLINE_RUBRICS = ("R1", "R6", "R3")
# A revision: instruction adherence, and the span's continuity with its
# surroundings (tpm-plan §3.4).
REVISE_RUBRICS = ("RA", "R5")
# Reported as the prose step's `book` block, outside every score (D18-4).
BOOK_RUBRICS = ("RN", "RC")

_INSTRUCTIONS = (
    "Score this artifact against the rubric above on 0.0-1.0. The anchors are "
    "fixed points: interpolate between them, and grade strictly (a draft that "
    "only nearly meets an anchor scores below it). Judge only what the rubric "
    "asks; the canon, style guide and plan given with the artifact are the "
    "ground truth to judge it against."
)


def rubric(rid: str) -> str:
    """The rubric text the judge receives for `rid` (R1..R6, RA, RN, RC)."""
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


# SPEC-94 S2a — None in a composite. A rubric's None is one of two things:
# "not applicable" (R2 with under two voiced cast speakers, RN without
# narration), which a composite has always left out of its mean; or "the judge
# did not score it" (an unparseable reply, a repeated "nothing was provided",
# the judge chain unavailable). The second makes the whole composite None — the
# item is counted unscored, never a mean over the rubrics that happened to
# parse, which would silently shift its score (R2 runs far below R3-R5).
# `unscored` names the rubric ids of that second kind (pipeline._unscored_ids).


def _any_unscored(rids, unscored) -> bool:
    return any(r in unscored for r in rids)


def scene_score(
    scores: dict[str, float | None], unscored: frozenset | set = frozenset()
) -> float | None:
    """R1 x mean(R2..R5); None when R1 or every other rubric is unscored, or
    when any of R1..R5 is in `unscored` (the judge did not score it)."""
    if _any_unscored(SCENE_RUBRICS, unscored):
        return None
    r1, rest = scores.get("R1"), _mean([scores.get(r) for r in SCENE_RUBRICS[1:]])
    if r1 is None or rest is None:
        return None
    return round(r1 * rest, 4)


def outline_score(
    scores: dict[str, float | None], unscored: frozenset | set = frozenset()
) -> float | None:
    """R1 x mean(R6, R3); None when R1 or both others are unscored, or when
    any of them is in `unscored`."""
    if _any_unscored(OUTLINE_RUBRICS, unscored):
        return None
    r1, rest = scores.get("R1"), _mean([scores.get("R6"), scores.get("R3")])
    if r1 is None or rest is None:
        return None
    return round(r1 * rest, 4)


def revise_score(
    scores: dict[str, float | None], unscored: frozenset | set = frozenset()
) -> float | None:
    """mean(RA, R5); None when neither is scored, or when either is in
    `unscored`."""
    if _any_unscored(REVISE_RUBRICS, unscored):
        return None
    return _round(_mean([scores.get(r) for r in REVISE_RUBRICS]))


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 4)
