"""Scene generation workflow.

Plain async pipeline: plan beats -> generate dialogue per beat -> assemble and
UPDATE the Scene row the API layer created (the API owns creation, with
user_id — the old create-twice-with-the-same-PK seam is gone).
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.characters.dialogue import generate_dialogue
from app.core.config import settings
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Character, Scene, SceneBeat
from app.core.sanitization import sanitize_for_llm
from app.llm.client import get_llm_client

logger = setup_logging("orchestration.workflow")


async def plan_scene_beats(
    scene_request: dict, user_id: Optional[UUID] = None
) -> list[dict]:
    """Break the scene into 3-5 narrative beats via one planning call."""
    characters_str = ", ".join(scene_request["characters"])
    scene_desc = sanitize_for_llm(scene_request["scene_description"], max_length=1000)
    setting = sanitize_for_llm(scene_request["setting"], max_length=500)
    emotional_tone = sanitize_for_llm(scene_request["emotional_tone"], max_length=100)

    prompt = f"""You are a narrative planner. Break down this scene into 3-5 narrative beats (smaller moments).

Scene Description: {scene_desc}
Setting: {setting}
Emotional Tone: {emotional_tone}
Characters: {characters_str}

For each beat, provide:
1. A brief description (1-2 sentences)
2. Which characters are active
3. The emotional subtext

Format your response as a numbered list of beats. Each beat should be on its own line starting with a number.

Beats:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500,
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


async def generate_beat_dialogue(
    beat: dict,
    scene_request: dict,
    character_ids: dict[str, str],
    user_id: Optional[UUID] = None,
) -> list[dict]:
    """Turn-based dialogue for one beat (characters rotate)."""
    max_turns = min(len(beat["characters"]) * 2, 8)
    dialogue_history: list[dict] = []

    for turn in range(max_turns):
        current = beat["characters"][turn % len(beat["characters"])]
        others = [c for c in beat["characters"] if c != current]
        try:
            response = await generate_dialogue(
                character_id=character_ids.get(current, ""),
                character_name=current,
                beat_description=beat["description"],
                scene_context={
                    "description": scene_request["scene_description"],
                    "setting": scene_request["setting"],
                    "emotional_tone": scene_request["emotional_tone"],
                },
                emotional_state=scene_request["emotional_tone"],
                other_characters=others,
                previous_dialogue=dialogue_history,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(
                f"Dialogue generation failed for {current}: {e}",
                extra_fields={"event": "dialogue_turn_failed", "character": current},
            )
            continue
        dialogue_history.append(
            {
                "character": current,
                "dialogue": response["dialogue"],
                "action": response.get("action", ""),
                "confidence": response.get("confidence_score", 0.0),
                "turn": turn,
            }
        )
    return dialogue_history


def assemble_scene_text(scene_request: dict, completed_beats: list[dict]) -> tuple:
    """Format the beats into markdown; returns (text, word_count)."""
    scene_text = f"# {scene_request.get('title', 'Generated Scene')}\n\n"
    scene_text += f"**Setting**: {scene_request['setting']}\n"
    scene_text += f"**Tone**: {scene_request['emotional_tone']}\n\n---\n\n"

    total_words = 0
    for beat_idx, beat in enumerate(completed_beats):
        scene_text += f"## Beat {beat_idx + 1}\n\n"
        for turn in beat["dialogue"]:
            if turn.get("action"):
                scene_text += f"*{turn['action']}*\n\n"
            scene_text += f"**{turn['character']}**: \"{turn['dialogue']}\"\n\n"
            total_words += len(turn["dialogue"].split())
        scene_text += "\n"
    return scene_text, total_words


async def run_scene_workflow(
    scene_id: UUID, scene_request: dict, user_id: UUID
) -> dict:
    """Full workflow for a Scene row the API layer already created.

    Updates the existing row (never inserts a second one) and records beats.
    """
    started = datetime.now(timezone.utc)
    try:
        character_ids = await _resolve_character_ids(
            scene_request["manuscript_id"], scene_request["characters"]
        )

        beats = await plan_scene_beats(scene_request, user_id)
        completed_beats = []
        for beat in beats:
            beat["dialogue"] = await generate_beat_dialogue(
                beat, scene_request, character_ids, user_id
            )
            completed_beats.append(beat)

        scene_text, word_count = assemble_scene_text(scene_request, completed_beats)
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        async with get_async_session() as session:
            scene = await session.get(Scene, scene_id)
            if scene is None:
                raise RuntimeError(f"Scene {scene_id} disappeared mid-workflow")
            scene.generated_content = scene_text
            scene.word_count = word_count
            scene.status = "completed"
            scene.generation_time_ms = elapsed_ms
            for beat_idx, beat in enumerate(completed_beats):
                session.add(
                    SceneBeat(
                        scene_id=scene.id,
                        beat_number=beat_idx,
                        description=beat["description"],
                        dialogue=beat["dialogue"],
                    )
                )

        log_business_event(
            logger,
            "scene_completed",
            scene_id=str(scene_id),
            beats_count=len(completed_beats),
            word_count=word_count,
            generation_time_ms=elapsed_ms,
        )
        return {
            "scene_id": str(scene_id),
            "status": "completed",
            "beats_count": len(completed_beats),
        }

    except Exception as e:
        log_error(
            logger, e, context={"scene_id": str(scene_id), "event": "scene_failed"}
        )
        try:
            async with get_async_session() as session:
                scene = await session.get(Scene, scene_id)
                if scene is not None:
                    scene.status = "failed"
        except Exception:
            pass
        return {"scene_id": str(scene_id), "status": "failed", "error": str(e)}


async def _resolve_character_ids(
    manuscript_id: str, character_names: list[str]
) -> dict[str, str]:
    """Map character names to their DB ids (for RAG payload filtering)."""
    async with get_async_session() as session:
        result = await session.execute(
            select(Character).where(Character.manuscript_id == UUID(str(manuscript_id)))
        )
        rows = result.scalars().all()
    by_name = {c.name: str(c.id) for c in rows}
    return {name: by_name.get(name, "") for name in character_names}
