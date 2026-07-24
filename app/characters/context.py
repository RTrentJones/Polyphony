"""Per-character voice-context assembly for prose-mode generation.

Prose mode writes a whole beat in ONE LLM call, so each involved character's
voice grounding (bible fields + retrieved voice samples) is assembled into a
compact context block instead of driving per-turn calls.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.llm_text import clean_for_llm
from app.core.orm_models import Character
from app.rag.store import get_chunk_store


async def load_characters_for_book(
    names: list[str],
    user_id: UUID,
    book_id: UUID,
) -> dict[str, Character]:
    """Character rows by name, scoped to one book and the requesting user.

    The book filter is now strict equality. It used to read

        (Character.book_id == book_id) | (Character.book_id.is_(None))

    which "worked" only because the IS NULL branch matched everything —
    `book_id` was never written, so every character was NULL-scoped and the
    filter was a no-op wearing a filter's clothes. With `book_id` NOT NULL there
    is nothing to fall back to, and the cast for a book is exactly its cast
    (docs/ADR-002-book-as-root.md §1).

    `user_id` stays as defence in depth even though `book_id` already implies an
    owner — the tenant guard from migration 0005.
    """
    async with get_async_session() as session:
        query = select(Character).where(
            Character.book_id == book_id,
            Character.user_id == user_id,
        )
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
        # The bible arrives whole. These fields used to be sliced to 300/200/200
        # chars — the same silent-truncation habit that cut the synopsis to 6.5%
        # and cost the outline its cast (docs/BRD.md §1). A full cast entry is
        # ~500 tokens against a 1M window.
        if character.role:
            lines.append(f"Role: {clean_for_llm(character.role)}")
        if character.description:
            lines.append(f"Description: {clean_for_llm(character.description)}")
        if character.goals:
            lines.append(f"Goals: {clean_for_llm(character.goals)}")
        if character.arc:
            lines.append(f"Arc: {clean_for_llm(character.arc)}")
        # Retrieve across ALL chunk types, not just "dialogue": the ingest-time
        # dialogue/action/thought classifier is heuristic and under-labels, so a
        # dialogue-only filter can starve voice grounding to nothing. Rank
        # dialogue first for display so spoken voice still leads.
        samples = await get_chunk_store().retrieve_similar(
            character_id=str(character.id),
            query=beat_description,
            k=max_samples,
            user_id=str(character.user_id) if character.user_id else None,
            book_id=str(character.book_id) if character.book_id else None,
        )
        samples.sort(key=lambda s: s.get("chunk_type") != "dialogue")
        if samples:
            lines.append("Voice samples (match this voice — cadence, diction, syntax):")
            # These samples ARE the voice grounding — the product's whole premise.
            # They used to be cut to 200 chars, which severed most of them
            # mid-sentence and taught the model half a cadence.
            lines.extend(f'- "{clean_for_llm(s["text"])}"' for s in samples)
    else:
        lines.append("(No bible entry — infer a consistent voice.)")
    return "\n".join(lines)


async def build_cast_context(
    names: list[str],
    beat_description: str,
    user_id: UUID,
    book_id: UUID,
) -> str:
    """Context blocks for every character in a beat, scoped to one book."""
    characters = await load_characters_for_book(names, user_id=user_id, book_id=book_id)
    blocks = []
    for name in names:
        blocks.append(
            await build_character_context(characters.get(name), name, beat_description)
        )
    return "\n\n".join(blocks)
