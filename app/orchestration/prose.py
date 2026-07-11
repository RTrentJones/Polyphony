"""Prose-mode scene generation: one LLM call per beat.

The turn-based workflow (workflow.py) costs 25-40 calls per scene — untenable
against Gemini's ~10 RPM free tier. Prose mode assembles every character's
voice context into a single beat prompt and writes the beat's full prose in
one call: 1 planning call + 3-5 beat calls per scene.

Used for generate-into-chapter; the turn-based path remains for standalone
scene generation and character voice testing.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.characters.context import build_cast_context
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Scene, SceneBeat
from app.core.sanitization import sanitize_for_llm
from app.llm.client import get_llm_client

from .workflow import plan_scene_beats

logger = setup_logging("orchestration.prose")

# Tail of the previous scene fed into the next one for continuity
PREV_TAIL_WORDS = 500


async def write_beat_prose(
    beat: dict,
    scene_request: dict,
    cast_context: str,
    prior_prose_tail: str,
    chapter_summary: str = "",
    user_id: Optional[UUID] = None,
    target_words: int = 300,
) -> str:
    """Write one beat's full prose (narration + in-voice dialogue) in one call."""
    setting = sanitize_for_llm(scene_request["setting"], max_length=500)
    tone = sanitize_for_llm(scene_request["emotional_tone"], max_length=100)
    beat_desc = sanitize_for_llm(beat["description"], max_length=1000)
    pov = scene_request.get("pov_character") or "third person limited"
    style_notes = scene_request.get("style_notes") or ""
    prior_block = (
        "The scene so far (continue seamlessly from this):\n" + prior_prose_tail
        if prior_prose_tail
        else "This beat opens the scene."
    )

    prompt = f"""You are a skilled novelist writing one beat of a scene.

{f"Chapter context: {chapter_summary}" if chapter_summary else ""}
Setting: {setting}
Emotional tone: {tone}
Point of view: {pov}
{f"Style notes: {style_notes}" if style_notes else ""}

CHARACTERS IN THIS BEAT — write each one's dialogue in THEIR OWN voice,
matching the voice samples exactly:

{cast_context}

{prior_block}

BEAT TO WRITE: {beat_desc}

Write approximately {target_words} words of polished narrative prose with
interwoven dialogue. Give each character a DISTINCT voice — their diction,
cadence, and syntax should differ enough that a reader could tell who is
speaking without the dialogue tags, grounded in the voice samples above. Do NOT
use headings, beat labels, or stage directions — just the prose. Do NOT repeat
the scene so far.

Prose:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=max(600, int(target_words * 2.2)),
        user_id=user_id,
        purpose="beat_prose",
    )
    return result.text


async def run_prose_scene_workflow(
    scene_id: UUID,
    scene_request: dict,
    user_id: UUID,
    chapter_summary: str = "",
    prior_scene_tail: str = "",
    book_id: Optional[UUID] = None,
) -> dict:
    """Prose-mode workflow for a Scene row the API layer already created."""
    started = datetime.now(timezone.utc)
    try:
        beats = await plan_scene_beats(scene_request, user_id)

        target_total = int(scene_request.get("target_word_count") or 500)
        per_beat_words = max(120, target_total // max(1, len(beats)))

        prose_parts: list[str] = []
        prior_tail = prior_scene_tail
        manuscript_id = scene_request.get("manuscript_id")

        for beat in beats:
            cast_context = await build_cast_context(
                beat["characters"],
                beat["description"],
                user_id=user_id,
                manuscript_id=UUID(str(manuscript_id)) if manuscript_id else None,
                book_id=book_id,
            )
            prose = await write_beat_prose(
                beat,
                scene_request,
                cast_context,
                prior_tail,
                chapter_summary=chapter_summary,
                user_id=user_id,
                target_words=per_beat_words,
            )
            prose_parts.append(prose)
            joined = "\n\n".join(prose_parts)
            prior_tail = " ".join(joined.split()[-PREV_TAIL_WORDS:])

        scene_text = "\n\n".join(prose_parts)
        word_count = len(scene_text.split())
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        # A safety block / truncation can make every beat return "" — don't record
        # blank output as a successful, editable draft.
        if word_count < 10:
            raise RuntimeError(
                f"scene generation produced no usable prose ({word_count} words)"
            )

        async with get_async_session() as session:
            scene = await session.get(Scene, scene_id)
            if scene is None:
                raise RuntimeError(f"Scene {scene_id} disappeared mid-workflow")
            scene.generated_content = scene_text
            scene.content = scene_text  # initial editable draft = the generation
            scene.word_count = word_count
            scene.status = "completed"
            scene.generation_time_ms = elapsed_ms
            for beat_idx, beat in enumerate(beats):
                session.add(
                    SceneBeat(
                        scene_id=scene.id,
                        beat_number=beat_idx,
                        description=beat["description"],
                        content=prose_parts[beat_idx],
                    )
                )

        log_business_event(
            logger,
            "prose_scene_completed",
            scene_id=str(scene_id),
            beats_count=len(beats),
            word_count=word_count,
            llm_calls=1 + len(beats),
        )
        return {
            "scene_id": str(scene_id),
            "status": "completed",
            "beats_count": len(beats),
        }

    except Exception as e:
        log_error(
            logger,
            e,
            context={"scene_id": str(scene_id), "event": "prose_scene_failed"},
        )
        try:
            async with get_async_session() as session:
                scene = await session.get(Scene, scene_id)
                if scene is not None:
                    scene.status = "failed"
        except Exception:
            pass
        return {"scene_id": str(scene_id), "status": "failed", "error": str(e)}
