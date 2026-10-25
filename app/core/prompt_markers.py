"""Prompt markers: the strings the app writes into a prompt that something parses.

A *marker* is a string the app writes into a prompt **that the prompt_contract
grader (`evals/graders/prompt_contract.py`) or the stub LLM
(`evals/stub_llm.py`) parses**. It is defined here once; the emitter and the
parser both import it. Prompt prose around a marker may change freely; a marker
changes only here.

This module lives in `app/core` and imports nothing from `app`. Instruction
wording that nothing parses stays in its emitter.

Frozen for the wave (day-0 contract); changes go through the orchestrator.
"""

from __future__ import annotations

import re

# --- the scene brief (orchestration/workflow.scene_block) ----------------------
SCENE_DESCRIPTION_LABEL = "Scene description"
SETTING_LABEL = "Setting"
EMOTIONAL_TONE_LABEL = "Emotional tone"
ALSO_NAMED_LABEL = "Also named (secondary, not in the cast)"
# orchestration/workflow.story_position_line; .format(n=<chapter>, m=<chapters>)
STORY_POSITION = "This scene is in chapter {n} of {m}."

# --- the beat planner (orchestration/workflow.plan_scene_beats) ----------------
AVAILABLE_CAST_LABEL = "Available cast (not all need appear)"

# --- point of view (orchestration/prose.pov_instruction, revise prompt) --------
POV_LABEL = "Point of view"
POV_LIMIT_OPEN = "Narrate only what"
POV_LIMIT_CLOSE = "perceives or knows"
# Omniscient and objective modes are not held to one head; their sentence opens
# with this instead of the limit.
POV_WIDE_OPEN = "The narrator"

# --- the beat prompt (orchestration/prose.write_beat_prose) --------------------
CHARACTERS_SECTION = "CHARACTERS IN THIS BEAT"
SCENE_CHARACTERS_SECTION = "CHARACTERS IN THIS SCENE"  # the revise prompt's cast
BEAT_TO_WRITE_LABEL = "BEAT TO WRITE"
CURRENT_BEAT_MARK = "→"
STOP_AT_NEXT_BEAT = "Stop where beat {next} begins"  # .format(next=<n + 1>)

# Fence labels (as_quoted_block(text, <label>) -> <label>...</label>).
BEATS_FENCE = "beats"
PRIOR_FENCE = "prior"
CHAPTER_FENCE = "chapter"

# --- the revise prompt: printed for a cut request (SPEC-59) --------------------
TIGHTEN_CLAUSE = (
    "Cut every word that adds nothing; keep every fact, image, joke, aside and "
    "voice marker in the passage."
)

# --- character blocks (characters/context, parsing/canon_extractor reduce) -----
NAME_HEADING = "### "  # followed by the character's name
VOICE_SAMPLES_LABEL = (
    "Voice samples (their own words, for manner only — match cadence, diction, "
    "syntax; never copy a sample's words: write new lines in this voice):"
)
# Own first-person narration standing in for speech (W17-1): labelled as
# what it is, not as their words aloud.
# The label's prefix; the tail depends on whether speech samples show too.
NARRATION_LABEL = "How they write (their own narration, for manner only) — "
NARRATION_WITH_SPEECH = (
    "their speech should lean toward this diction, sentence length and period; "
    "don't modernise it beyond what their quoted speech shows. Never copy a "
    "sample's words:"
)
NARRATION_ONLY = (
    "their speech keeps this diction, sentence length and period; do not "
    "modernise or clip it, and never copy a sample's words:"
)
# A beat with 2+ sampled speakers: one computed line each (W17-1).
VOICE_CONTRAST_NOTE = "How their voices differ (computed from the samples above):"
EVIDENCE_LABEL = "Evidence:"
EVIDENCE_ITEM = "- (part {part}) {quote}"  # .format(part=<1-based>, quote=...)
# A member left with no verbatim quote (SPEC-56, SPEC-64): a mark, not a quote;
# "narrates" on the narrator path only. Both .format(part=<1-based>).
EVIDENCE_STAND_IN = "narrates part {part}"
EVIDENCE_NO_QUOTE = "no verbatim quote in part {part}"
QUOTED_LINES_LABEL = "Quoted lines (their own words):"
MOVEMENT_LABEL = (
    "How they move (gesture and habit, for manner only: never copy a sample's "
    "words, and do not reuse these events, objects or names):"
)
# The rules a grounding record names for a movement line it withheld, in
# `moves`' predicate order (SPEC-69). Not printed: the movement grader reads it.
WITHHELD_RULES = ("later_plot", "not_centred", "names_outside_scene", "restates")
# The record's key for the withheld entries dropped past its per-owner cap
# (SPEC-69 amendment); the export and the carried rule read it.
WITHHELD_MORE = "withheld_more"

# --- secondary characters (planning/secondaries.render_secondaries) ------------
SECONDARY_FENCE = "secondary"
INTRODUCED_LABEL = "Introduced:"

# --- the staged outline (planning/staged_outline) ------------------------------
CONFIRMED_PREMISE_NOTE = " (confirmed by the author: treat it as authoritative)"
ACT_CHAPTERS_HEADER = "Here are the chapters of act {number}, in order:"  # .format
OUTLINE_CHAPTERS_HEADER = (
    "The whole book, chapter by chapter (→ marks this act's chapters):"
)
# .format(what=<"earlier acts" | "the earlier scenes of this chapter">)
EARLIER_BEATS = "Beats already planned in {what}:"
# The chapter's planned scenes for the beat planner (orchestration/workflow).
CHAPTER_SCENES_HEADER = (
    "This chapter's planned scenes, in order (→ marks the one being planned):"
)
# An outline warning from the check against the author's sources (SPEC-73):
# the outline job writes it, `outline_source_conflicts` reads it. A prefix.
OUTLINE_SOURCE_WARNING = "Outline vs your source: "


# --- parsers built from the markers --------------------------------------------


def labelled_line(label: str) -> re.Pattern:
    """`^<label>: (.+)$`, multiline."""
    return re.compile(rf"(?m)^{re.escape(label)}: (.+)$")


# who may contain a period ("Dr. Ada Vance"): anchor on the limit sentence (S5).
POV_LINE_RE = re.compile(
    rf"(?m)^{re.escape(POV_LABEL)}: "
    r"(?P<mode>(?:first|second|third) person[^,\n]*?)(?:, (?P<who>[^\n]+?))?"
    rf"\.\s+(?:{re.escape(POV_LIMIT_OPEN)}|{re.escape(POV_WIDE_OPEN)})"
)
STORY_POSITION_RE = re.compile(
    re.escape(STORY_POSITION)
    .replace(r"\{n\}", r"(?P<n>\d+)")
    .replace(r"\{m\}", r"(?P<m>\d+)")
)
