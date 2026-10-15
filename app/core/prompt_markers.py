"""Prompt markers: the strings the app writes into a prompt that something parses.

A *marker* is a string the app writes into a prompt **that the prompt_contract
grader (`evals/graders/prompt_contract.py`) or the stub LLM
(`evals/stub_llm.py`) parses**. It is defined here once; the emitter and the
parser both import it. Prompt prose around a marker may change freely; a marker
changes only here.

This module lives in `app/core` and imports nothing from `app`. Instruction
wording that nothing parses stays in its emitter.

Frozen for wave 2.5 (day-0 contract); changes go through the orchestrator.
"""

from __future__ import annotations

import re

# --- the scene brief (orchestration/workflow.scene_block) ----------------------
SCENE_DESCRIPTION_LABEL = "Scene description"
SETTING_LABEL = "Setting"
EMOTIONAL_TONE_LABEL = "Emotional tone"

# --- the beat planner (orchestration/workflow.plan_scene_beats) ----------------
AVAILABLE_CAST_LABEL = "Available cast (not all need appear)"

# --- point of view (orchestration/prose.pov_instruction, revise prompt) --------
POV_LABEL = "Point of view"
POV_LIMIT_OPEN = "Narrate only what"
POV_LIMIT_CLOSE = "perceives or knows"

# --- the beat prompt (orchestration/prose.write_beat_prose) --------------------
CHARACTERS_SECTION = "CHARACTERS IN THIS BEAT"
BEAT_TO_WRITE_LABEL = "BEAT TO WRITE"
CURRENT_BEAT_MARK = "→"
STOP_AT_NEXT_BEAT = "Stop where beat {next} begins"  # .format(next=<n + 1>)

# Fence labels (as_quoted_block(text, <label>) -> <label>...</label>).
BEATS_FENCE = "beats"
PRIOR_FENCE = "prior"
CHAPTER_FENCE = "chapter"

# --- character blocks (characters/context, parsing/canon_extractor reduce) -----
NAME_HEADING = "### "  # followed by the character's name
VOICE_SAMPLES_LABEL = (
    "Voice samples (their own words — match cadence, diction, syntax):"
)
EVIDENCE_LABEL = "Evidence:"
EVIDENCE_ITEM = "- (part {part}) {quote}"  # .format(part=<1-based>, quote=...)
QUOTED_LINES_LABEL = "Quoted lines (their own words):"

# --- the staged outline (planning/staged_outline) ------------------------------
CONFIRMED_PREMISE_NOTE = " (confirmed by the author: treat it as authoritative)"
ACT_CHAPTERS_HEADER = "Here are the chapters of act {number}, in order:"  # .format


# --- parsers built from the markers --------------------------------------------


def labelled_line(label: str) -> re.Pattern:
    """`^<label>: (.+)$`, multiline."""
    return re.compile(rf"(?m)^{re.escape(label)}: (.+)$")


# who may contain a period ("Dr. Ada Vance"): anchor on the limit sentence (S5).
POV_LINE_RE = re.compile(
    rf"(?m)^{re.escape(POV_LABEL)}: "
    r"(?P<mode>(?:first|second|third) person[^,\n]*?)(?:, (?P<who>[^\n]+?))?"
    rf"\.\s+{re.escape(POV_LIMIT_OPEN)}"
)
