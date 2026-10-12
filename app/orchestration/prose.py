"""Prose-mode scene generation: one LLM call per beat.

Prose mode assembles every character's voice context into a single beat prompt
and writes the beat's full prose in one call: 1 planning call + 3-5 beat calls
per scene. It is the default mode and the only one the free tier allows.

Every call carries the book's whole Canon — synopsis, cast, canon entries and
style guide — fenced as story material (SPEC-01; docs/BRD.md R1.1, R2.3). This
path used to see only the request form, so a book whose style guide said "first
person, present tense" was written however the form happened to say, and a
scene set in Aeon Holdings knew nothing about Aeon Holdings.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.characters.context import build_cast_context
from app.core.database import get_async_session
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Scene, SceneBeat
from app.llm.client import get_llm_client
from app.llm.errors import QuotaExhaustedError

from .workflow import plan_scene_beats, scene_block

logger = setup_logging("orchestration.prose")

# Tail of the previous scene fed into the next one for continuity
PREV_TAIL_WORDS = 500


def scene_cast(canon, scene_request: dict) -> list:
    """The scene's cast as Character rows from the book's Canon.

    Resolved by ID (the API validated them against the book); legacy payloads
    that carry only names resolve by exact name. A row that is not in this book's
    Canon is never invented."""
    ids = {str(i) for i in scene_request.get("character_ids") or []}
    by_id = {str(c.id): c for c in canon.characters}
    if ids:
        return [by_id[i] for i in scene_request.get("character_ids") if i in by_id]
    names = scene_request.get("characters") or []
    by_name = {c.name: c for c in canon.characters}
    return [by_name[n] for n in names if n in by_name]


def resolve_pov(scene_request: dict, canon) -> str:
    """The request's POV wins; else the book's style guide; else a default."""
    if scene_request.get("pov_character"):
        return clean_for_llm(scene_request["pov_character"])
    style = getattr(canon, "style", None)
    if style is not None and getattr(style, "pov", None):
        return f"as the book's style guide specifies ({clean_for_llm(style.pov)})"
    return "third person limited"


async def write_beat_prose(
    beat: dict,
    scene_request: dict,
    cast_context: str,
    prior_prose_tail: str,
    chapter_summary: str = "",
    user_id: Optional[UUID] = None,
    target_words: int = 300,
    *,
    canon_block: str = "",
    pov: str = "third person limited",
) -> str:
    """Write one beat's full prose (narration + in-voice dialogue) in one call.

    Prompt layout: a stable prefix (notice, whole canon, chapter) that is the
    same for every beat of the scene, then the per-beat suffix (scene brief,
    the beat's cast, the prose so far, the beat itself).
    """
    style_notes = scene_request.get("style_notes") or ""
    prior_block = (
        "The scene so far (continue seamlessly from it):\n"
        + as_quoted_block(prior_prose_tail, "prior")
        if prior_prose_tail
        else "This beat opens the scene."
    )
    chapter = as_quoted_block(chapter_summary, "chapter")
    overrides = (
        "\nAuthor's style notes for THIS scene (they override the style guide):\n"
        + as_quoted_block(style_notes, "style_notes")
        if style_notes
        else ""
    )

    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_block}

{chapter}

You are a skilled novelist writing one beat of a scene in THIS book. Stay
faithful to the canon above: its people, places, terms, and its style guide
(point of view, tense, tone) unless the author's notes below override it.

{scene_block(scene_request)}
Point of view: {pov}{overrides}

CHARACTERS IN THIS BEAT — write each one's dialogue in THEIR OWN voice,
matching their voice samples:

{cast_context}

{prior_block}

BEAT TO WRITE: {clean_for_llm(beat["description"])}

Write approximately {target_words} words of polished NARRATIVE PROSE. Ground the
reader in the setting and in the characters' actions, gestures, expressions, and
interiority — description and action carry the scene; dialogue PUNCTUATES it, it
does not dominate. When a character does speak, give each a DISTINCT voice —
diction, cadence, and syntax differing enough that a reader could tell who is
speaking without the tags. Do NOT use headings, beat labels, character-name
prefixes (e.g. "Milo:"), or stage directions — just the prose. Do NOT repeat the
scene so far.

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
    *,
    checkpoint: Optional[dict] = None,
    save_checkpoint=None,
) -> dict:
    """Prose-mode workflow for a Scene row the API layer already created.

    `checkpoint`/`save_checkpoint` make a resumed job continue from its last
    completed model call instead of re-spending every beat (SPEC-08).
    """
    from app.planning.canon import build_canon

    started = datetime.now(timezone.utc)
    checkpoint = dict(checkpoint or {})
    try:
        if book_id is None:
            raise RuntimeError("scene generation needs its book (book is the root)")
        async with get_async_session() as session:
            canon = await build_canon(session, book_id)
        canon_block = canon.full_block()
        cast = scene_cast(canon, scene_request)
        names = [c.name for c in cast]
        request = {**scene_request, "characters": names}
        pov = resolve_pov(scene_request, canon)
        canon_hash = _canon_hash(canon_block, chapter_summary)

        if checkpoint.get("canon_hash") not in (None, canon_hash):
            # The canon changed while the job was paused: stages generated
            # against two canons would be worse than paying twice.
            logger.info(
                "Canon changed during pause; restarting scene from scratch",
                extra_fields={"event": "scene_checkpoint_discarded"},
            )
            checkpoint = {}

        beats = checkpoint.get("beats")
        if beats is None:
            beats = await plan_scene_beats(
                request,
                user_id,
                canon_block=canon_block,
                chapter_summary=chapter_summary,
            )
            checkpoint = {"canon_hash": canon_hash, "beats": beats, "prose": []}
            await _save(save_checkpoint, checkpoint)

        target_total = int(scene_request.get("target_word_count") or 500)
        per_beat_words = max(120, target_total // max(1, len(beats)))
        by_name = {c.name: c for c in cast}

        prose_parts: list[str] = list(checkpoint.get("prose") or [])
        grounding: dict = dict(checkpoint.get("grounding") or {})
        prior_tail = prior_scene_tail
        if prose_parts:
            prior_tail = " ".join("\n\n".join(prose_parts).split()[-PREV_TAIL_WORDS:])

        for idx in range(len(prose_parts), len(beats)):
            beat = beats[idx]
            beat_cast = [by_name[n] for n in beat["characters"] if n in by_name]
            beat_grounding: list = []
            cast_context = await build_cast_context(
                beat_cast, beat["description"], grounding=beat_grounding
            )
            prose = await write_beat_prose(
                beat,
                request,
                cast_context,
                prior_tail,
                chapter_summary=chapter_summary,
                user_id=user_id,
                target_words=per_beat_words,
                canon_block=canon_block,
                pov=pov,
            )
            prose_parts.append(prose)
            grounding[str(idx)] = beat_grounding
            joined = "\n\n".join(prose_parts)
            prior_tail = " ".join(joined.split()[-PREV_TAIL_WORDS:])
            checkpoint.update(prose=prose_parts, grounding=grounding)
            await _save(save_checkpoint, checkpoint)

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
            scene.evaluation_scores = {
                **(scene.evaluation_scores or {}),
                "mode": "prose",
                "grounding": grounding,
            }
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

    except QuotaExhaustedError:
        # Not a failure — the quota is temporarily gone. Let the error propagate
        # so the worker PAUSES the job and resumes it (from its checkpoint) when
        # quota returns (docs/BRD.md R7.2).
        raise
    except Exception as e:
        from app.jobs.cancel import JobCancelled

        if isinstance(e, JobCancelled):
            raise
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


def _canon_hash(canon_block: str, chapter_summary: str) -> str:
    import hashlib

    return hashlib.sha256(
        (canon_block + "\x00" + (chapter_summary or "")).encode("utf-8")
    ).hexdigest()[:16]


async def _save(save_checkpoint, data: dict) -> None:
    if save_checkpoint is not None:
        await save_checkpoint(data)
