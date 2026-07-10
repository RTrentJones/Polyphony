"""Per-character voice-context assembly for prose-mode generation.

Prose mode writes a whole beat in ONE LLM call, so each involved character's
voice grounding (bible fields + retrieved voice samples) is assembled into a
compact context block instead of driving per-turn calls.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.orm_models import Character
from app.core.sanitization import sanitize_for_llm
from app.rag.store import get_chunk_store


async def load_characters_for_book_or_manuscript(
    names: list[str],
    user_id: UUID,
    manuscript_id: Optional[UUID] = None,
    book_id: Optional[UUID] = None,
) -> dict[str, Character]:
    """Character rows by name, scoped to the requesting user.

    ownership is ALWAYS enforced (directly via user_id, or via the owning
    manuscript for legacy extracted rows that predate user_id backfill) — the
    book/manuscript filters only narrow within the user's own bible. Without
    the user scope, book_id-is-null characters would match across all tenants.
    """
    async with get_async_session() as session:
        from app.core.orm_models import Manuscript

        query = (
            select(Character)
            .outerjoin(Manuscript, Character.manuscript_id == Manuscript.id)
            .where((Character.user_id == user_id) | (Manuscript.user_id == user_id))
        )
        if book_id is not None:
            query = query.where(
                (Character.book_id == book_id) | (Character.book_id.is_(None))
            )
        if manuscript_id is not None:
            query = query.where(Character.manuscript_id == manuscript_id)
        rows = (await session.execute(query)).scalars().all()
    by_name = {c.name: c for c in rows}
    return {name: by_name[name] for name in names if name in by_name}


async def build_character_context(
    character: Optional[Character],
    name: str,
    beat_description: str,
    max_samples: int = 5,
) -> str:
    """One character's context block: bible summary + retrieved voice samples."""
    lines = [f"### {name}"]
    if character is not None:
        if character.role:
            lines.append(f"Role: {character.role}")
        if character.description:
            lines.append(f"Description: {character.description[:300]}")
        if character.goals:
            lines.append(f"Goals: {character.goals[:200]}")
        if character.arc:
            lines.append(f"Arc: {character.arc[:200]}")
        # Retrieve across ALL chunk types, not just "dialogue": the ingest-time
        # dialogue/action/thought classifier is heuristic and under-labels, so a
        # dialogue-only filter can starve voice grounding to nothing. Rank
        # dialogue first for display so spoken voice still leads.
        samples = await get_chunk_store().retrieve_similar(
            character_id=str(character.id),
            query=beat_description,
            k=max_samples,
            user_id=str(character.user_id) if character.user_id else None,
        )
        samples.sort(key=lambda s: s.get("chunk_type") != "dialogue")
        if samples:
            lines.append("Voice samples (match this voice — cadence, diction, syntax):")
            # Retrieved text is user content — sanitize before it enters the prompt.
            lines.extend(
                f'- "{sanitize_for_llm(s["text"], max_length=200)}"' for s in samples
            )
    else:
        lines.append("(No bible entry — infer a consistent voice.)")
    return "\n".join(lines)


async def build_cast_context(
    names: list[str],
    beat_description: str,
    user_id: UUID,
    manuscript_id: Optional[UUID] = None,
    book_id: Optional[UUID] = None,
) -> str:
    """Context blocks for every character in a beat (scoped to the user)."""
    characters = await load_characters_for_book_or_manuscript(
        names, user_id=user_id, manuscript_id=manuscript_id, book_id=book_id
    )
    blocks = []
    for name in names:
        blocks.append(
            await build_character_context(characters.get(name), name, beat_description)
        )
    return "\n\n".join(blocks)
