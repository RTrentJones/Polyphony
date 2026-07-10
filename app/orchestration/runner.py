"""Background scene-generation runner.

A process-wide semaphore(1) serializes scene workflows so two users generating
at once queue behind each other instead of splitting the provider's free-tier
RPM budget (the per-call pacer in app/llm/pacing.py handles the fine grain).
"""

import asyncio
from uuid import UUID

from app.core.logging_config import setup_logging

logger = setup_logging("orchestration.runner")

_scene_semaphore = asyncio.Semaphore(1)


async def run_scene_in_background(
    scene_id: UUID, scene_request: dict, user_id: UUID
) -> dict:
    """Turn-based workflow entry point (standalone scenes)."""
    from .workflow import run_scene_workflow

    async with _scene_semaphore:
        return await run_scene_workflow(scene_id, scene_request, user_id)


async def run_prose_scene_in_background(
    scene_id: UUID,
    scene_request: dict,
    user_id: UUID,
    chapter_summary: str = "",
    prior_scene_tail: str = "",
    book_id: UUID | None = None,
) -> dict:
    """Prose-mode workflow entry point (generate-into-chapter)."""
    from .prose import run_prose_scene_workflow

    async with _scene_semaphore:
        return await run_prose_scene_workflow(
            scene_id,
            scene_request,
            user_id,
            chapter_summary=chapter_summary,
            prior_scene_tail=prior_scene_tail,
            book_id=book_id,
        )
