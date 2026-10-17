"""Scene beat planning — shared by the prose and ensemble scene workflows.

The classic dialogue-turn generator (plan -> per-character dialogue -> "## Beat"
markdown) was removed with the standalone `POST /scenes/generate` endpoint: scenes
are generated in-book into a chapter (docs/BRD.md §8). This module keeps only the
beat planner both paths share.

The planner sees the book's whole Canon (SPEC-01, BRD R1.1) and returns
STRUCTURED beats, each with its own cast — a subset of the scene's validated
cast. It used to see only the request form and assign the full cast to every
beat, throwing away the planner's own casting (eval-audit backlog item 5).
"""

from __future__ import annotations

from typing import NamedTuple, Optional
from uuid import UUID

from app.core.config import settings
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import setup_logging
from app.core.prompt_markers import (
    ALSO_NAMED_LABEL,
    AVAILABLE_CAST_LABEL,
    CHAPTER_FENCE,
    CHAPTER_SCENES_HEADER,
    EARLIER_BEATS,
    EMOTIONAL_TONE_LABEL,
    SCENE_DESCRIPTION_LABEL,
    SETTING_LABEL,
    STORY_POSITION,
)
from app.llm.client import get_llm_client
from app.llm.json_utils import JSON_OBJECT, extract_json_array
from app.orchestration.pov import Pov, plan_pov_instruction
from app.planning.planned_beat import beat_scene_description, marked_plan

logger = setup_logging("orchestration.workflow")


class StoryPosition(NamedTuple):  # SPEC-30
    chapter: int  # Chapter.number
    of: int  # live chapters in the book


def story_position_line(position: Optional[StoryPosition]) -> str:
    """The synopsis tells the whole book: say how much of it has happened."""
    if position is None:
        return ""
    return (
        f"{STORY_POSITION.format(n=position.chapter, m=position.of)} The synopsis "
        "tells the whole book; nothing after this chapter has happened yet. Do not "
        "mention, hint at or set up later events."
    )


def chapter_context(chapter_summary: str, position: Optional[StoryPosition]) -> str:
    """`<chapter>`, then the story-position line right after it."""
    parts = (
        as_quoted_block(chapter_summary, CHAPTER_FENCE),
        story_position_line(position),
    )
    return "\n".join(p for p in parts if p)


def chapter_plan_block(plan, story_position, scene_request, earlier) -> str:
    """This chapter's planned scenes, the one being planned marked, and the
    beat lines its earlier scenes already planned (SPEC-43); "" without a plan."""
    chapter = story_position.chapter if story_position else None
    beats = [b for n, b in plan if n == chapter]
    if not beats:
        return ""
    here = scene_request.get("planned_beat_id")
    current = {i for i, b in enumerate(beats) if b["node_id"] == here}
    header = CHAPTER_SCENES_HEADER  # an unplanned scene: no "→ marks" claim
    lines = marked_plan(
        header if current else header.split(" (")[0] + ":",
        [_one_line(beat_scene_description(b)) for b in beats],
        current,
        EARLIER_BEATS.format(what="the earlier scenes of this chapter"),
        [f"- {_one_line(t)}: {_one_line(' '.join(ls))}" for t, ls in earlier],
    )
    if earlier:
        lines.append(
            "Keep the days, times and objects these establish; do not introduce "
            "again what an earlier scene already introduced."
        )
    return "\n".join(lines)


def _one_line(value) -> str:
    return " ".join(clean_for_llm(str(value or "")).split())


def secondary_names(scene_request: dict) -> list[str]:
    """The request's named secondaries (SPEC-23 passes the beat's unresolved
    ones): trimmed, deduped, in order."""
    raw = scene_request.get("secondary_names")
    out: list[str] = []
    for name in raw if isinstance(raw, list) else []:
        name = _one_line(name)
        if name and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


def scene_block(scene_request: dict) -> str:
    """The author's scene brief, fenced. It is first-party, but it is free text
    and can carry pasted material, so it is fenced like everything else.

    A blank setting and a blank tone are left out rather than printed empty:
    a bare "Setting:" invites the model to invent one, and a blank tone leaves
    the style guide's tone to govern (SPEC-24). A tone the author chose,
    "neutral" included, is kept (review S7). Named secondaries the
    beat declares are listed so they may appear by name without being cast."""
    parts = [
        f"{SCENE_DESCRIPTION_LABEL}: "
        f"{clean_for_llm(scene_request.get('scene_description'))}"
    ]
    setting = _one_line(scene_request.get("setting"))
    if setting:
        parts.append(f"{SETTING_LABEL}: {setting}")
    tone = _one_line(scene_request.get("emotional_tone"))
    if tone:
        parts.append(f"{EMOTIONAL_TONE_LABEL}: {tone}")
    secondaries = secondary_names(scene_request)
    if secondaries:
        parts.append(f"{ALSO_NAMED_LABEL}: {', '.join(secondaries)}")
    return as_quoted_block("\n".join(parts), "scene")


# Only with an Also-named list: alone, it read as a missing list (SPEC-43).
ALSO_NAMED_NOTE = (
    ' A person listed as "Also named" in the scene brief may appear by name, but'
    " is not listed in a beat's characters."
)


# Beside the POV line (SPEC-61): a beat says what is said, never the author's words.
DESCRIBE_NOT_QUOTE = (
    "Describe what people say; do not quote lines from the author's source or "
    "samples."
)


async def plan_scene_beats(
    scene_request: dict,
    user_id: Optional[UUID] = None,
    *,
    canon_block: str,
    chapter_summary: str,
    pov: Pov,
    story_position: Optional[StoryPosition],
    secondary_block: str,
    plan: list,
    earlier: list,
) -> list[dict]:
    """Break the scene into 3-5 beats via one planning call.

    Returns ``[{"description", "characters": [names]}]`` where each beat's cast
    is a validated subset of the scene's cast (an empty or invalid subset falls
    back to the whole scene cast — never to an invented name); a non-JSON
    answer gives one whole-scene beat.

    The cast is offered as AVAILABLE, not mandatory (SPEC-24, W2-1): the old
    "the scene's cast is exactly" made every beat of a Milo-and-a-caseworker
    scene a Milo-and-Zara scene. `plan`/`earlier` are SceneSetup's (SPEC-43).
    """
    cast: list[str] = list(scene_request.get("characters") or [])
    also_named = ALSO_NAMED_NOTE if secondary_names(scene_request) else ""
    chapter_plan = chapter_plan_block(plan, story_position, scene_request, earlier)
    chapter_plan += "\n\n" if chapter_plan else ""
    pov_and_secondaries = "\n\n".join(
        p
        for p in (f"{plan_pov_instruction(pov)}\n{DESCRIBE_NOT_QUOTE}", secondary_block)
        if p
    )
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_block}

{chapter_context(chapter_summary, story_position)}

{chapter_plan}{scene_block(scene_request)}
{pov_and_secondaries}

You are a narrative planner. Break this scene of THIS book into 3-5 beats
(smaller moments), consistent with the canon above.
{AVAILABLE_CAST_LABEL}: {", ".join(cast) or "(none)"}
These are the characters who MAY appear; a beat need not use all of them. Each
beat lists which of them are present in it — use only names from that list.{also_named}
Unnamed functional figures (a caseworker, a clerk, a guard) may appear without a
name; do not invent names for them.

Return ONLY a JSON object:
{{"items": [{{"description": "what happens, plus its emotional subtext, in one sentence",
   "characters": ["names present in this beat"]}}]}}

JSON:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1200,
        user_id=user_id,
        purpose="plan_beats",
        response_format=JSON_OBJECT,
    )

    beats = parse_structured_beats(result.text, cast)
    if not beats:
        logger.warning(
            "Planner answer is not a JSON beat list; one whole-scene beat",
            extra_fields={"event": "plan_beats_unparsed"},
        )
        beats = [
            {
                "description": scene_request["scene_description"],
                "characters": cast,
                "dialogue": [],
            }
        ]
    return beats[: settings.MAX_SCENE_BEATS]


def parse_structured_beats(text: str, cast: list[str]) -> list[dict]:
    """Parse the JSON planner response, keeping each beat's cast inside `cast`."""
    try:
        raw = extract_json_array(text)
    except ValueError:
        return []
    allowed = {c.lower(): c for c in cast}
    beats = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description", "") or "").strip()
        if not desc:
            continue
        names = item.get("characters") or []
        present = []
        for n in names if isinstance(names, list) else []:
            match = allowed.get(str(n).strip().lower())
            if match and match not in present:
                present.append(match)
        dropped = [n for n in names if str(n).strip().lower() not in allowed]
        if dropped:
            logger.warning(
                "Planner named characters outside the scene cast; dropped",
                extra_fields={"event": "beat_cast_dropped", "names": dropped},
            )
        beats.append(
            {"description": desc, "characters": present or list(cast), "dialogue": []}
        )
    return beats
