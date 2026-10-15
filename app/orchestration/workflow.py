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

from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import setup_logging
from app.core.prompt_markers import (
    ALSO_NAMED_LABEL,
    AVAILABLE_CAST_LABEL,
    CHAPTER_FENCE,
    EMOTIONAL_TONE_LABEL,
    SCENE_DESCRIPTION_LABEL,
    SETTING_LABEL,
)
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array

logger = setup_logging("orchestration.workflow")


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


async def plan_scene_beats(
    scene_request: dict,
    user_id: Optional[UUID] = None,
    *,
    canon_block: str = "",
    chapter_summary: str = "",
) -> list[dict]:
    """Break the scene into 3-5 beats via one planning call.

    Returns ``[{"description", "characters": [names]}]`` where each beat's cast
    is a validated subset of the scene's cast (an empty or invalid subset falls
    back to the whole scene cast — never to an invented name).

    The cast is offered as AVAILABLE, not mandatory (SPEC-24, W2-1): the old
    "the scene's cast is exactly" made every beat of a Milo-and-a-caseworker
    scene a Milo-and-Zara scene.
    """
    cast: list[str] = list(scene_request.get("characters") or [])
    chapter = as_quoted_block(chapter_summary, CHAPTER_FENCE)
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_block}

{chapter}

{scene_block(scene_request)}

You are a narrative planner. Break this scene of THIS book into 3-5 beats
(smaller moments), consistent with the canon above.
{AVAILABLE_CAST_LABEL}: {", ".join(cast) or "(none)"}
These are the characters who MAY appear; a beat need not use all of them. Each
beat lists which of them are present in it — use only names from that list. A
person listed as "Also named" in the scene brief may appear by name, but is not
listed in a beat's characters. Unnamed functional figures (a caseworker, a
clerk, a guard) may appear without a name; do not invent names for them.

Return ONLY a JSON array:
[{{"description": "what happens, plus its emotional subtext, in one sentence",
   "characters": ["names present in this beat"]}}]

JSON:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1200,
        user_id=user_id,
        purpose="plan_beats",
    )

    beats = parse_structured_beats(result.text, cast)
    if not beats:
        beats = parse_beats(result.text, cast)
    if not beats:
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


def parse_beats(beats_text: str, characters: list[str]) -> list[dict]:
    """Fallback: parse a numbered-list planning response into beat dicts."""
    beats = []
    for line in beats_text.split("\n"):
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith("-")):
            beat_desc = line.split(".", 1)[-1].strip()
            if beat_desc:
                beats.append(
                    {
                        "description": beat_desc,
                        "characters": characters,
                        "dialogue": [],
                    }
                )
    return beats
