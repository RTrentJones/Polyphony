"""Scene beat planning — shared by the prose scene workflow (app/orchestration/
prose.py).

The classic dialogue-turn generator (plan -> per-character dialogue -> "## Beat"
markdown) was removed with the standalone `POST /scenes/generate` endpoint: scenes
are prose-only now, generated in-book into a chapter (docs/BRD.md §8). This module
keeps only the beat planner both paths shared.
"""

from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.llm_text import clean_for_llm
from app.llm.client import get_llm_client


async def plan_scene_beats(
    scene_request: dict, user_id: Optional[UUID] = None
) -> list[dict]:
    """Break the scene into 3-5 narrative beats via one planning call."""
    characters_str = ", ".join(scene_request["characters"])
    scene_desc = clean_for_llm(scene_request["scene_description"])
    setting = clean_for_llm(scene_request["setting"])
    emotional_tone = clean_for_llm(scene_request["emotional_tone"])

    prompt = f"""You are a narrative planner. Break down this scene into 3-5 narrative beats (smaller moments).

Scene Description: {scene_desc}
Setting: {setting}
Emotional Tone: {emotional_tone}
Characters: {characters_str}

Return ONE beat per line, each starting with a number, and put the WHOLE beat on
that single line: a brief description of what happens plus its emotional subtext.
Do NOT add sub-bullets, sub-numbering, or fields on separate lines.

Example:
1. Alice confronts Bob about the missing letter; her calm masks fury.
2. Bob deflects, then admits the truth, and the room turns cold.

Beats:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1000,
        user_id=user_id,
        purpose="plan_beats",
    )

    beats = parse_beats(result.text, scene_request["characters"])
    if not beats:
        beats = [
            {
                "description": scene_request["scene_description"],
                "characters": scene_request["characters"],
                "dialogue": [],
            }
        ]
    return beats[: settings.MAX_SCENE_BEATS]


def parse_beats(beats_text: str, characters: list[str]) -> list[dict]:
    """Parse the numbered-list planning response into beat dicts."""
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
