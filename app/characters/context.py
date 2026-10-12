"""Per-character voice-context assembly for scene generation.

Prose mode writes a whole beat in ONE LLM call, so each involved character's
voice grounding (bible fields + retrieved voice samples) is assembled into a
compact context block instead of driving per-turn calls.

The cast arrives as Character ROWS, already resolved and validated against the
book at the API (SPEC-01). It used to arrive as free-text names, and a name that
matched nothing became "(No bible entry — infer a consistent voice.)": a typo
silently produced voice-blind prose. That branch is gone because it is now
unreachable.
"""

from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.llm_text import as_quoted_block, clean_for_llm
from app.core.orm_models import Character
from app.rag.store import get_chunk_store


async def load_characters_for_book(
    names: list[str],
    user_id: UUID,
    book_id: UUID,
) -> dict[str, Character]:
    """Character rows by name, scoped to one book and the requesting user.

    The book filter is strict equality. It used to read

        (Character.book_id == book_id) | (Character.book_id.is_(None))

    which "worked" only because the IS NULL branch matched everything —
    `book_id` was never written, so every character was NULL-scoped and the
    filter was a no-op wearing a filter's clothes (docs/ADR-002-book-as-root.md
    §1). `user_id` stays as defence in depth — the tenant guard from 0005.
    """
    async with get_async_session() as session:
        query = select(Character).where(
            Character.book_id == book_id,
            Character.user_id == user_id,
            Character.deleted_at.is_(None),  # trashed: not castable (SPEC-13)
        )
        rows = (await session.execute(query)).scalars().all()
    by_name = {c.name: c for c in rows}
    return {name: by_name[name] for name in names if name in by_name}


async def build_character_context(
    character: Optional[Character],
    name: str,
    beat_description: str,
    max_samples: int = 5,
    *,
    chunk_types: Optional[Iterable[str]] = None,
    grounding: Optional[list] = None,
) -> str:
    """One character's context block: bible summary + retrieved voice samples.

    `grounding`, when given, collects the retrieved chunks (id + score) so the
    caller can record which samples grounded this generation (R5.3).
    """
    lines = [f"### {name}"]
    if character is None:
        return "\n".join(lines)
    # The bible arrives whole. These fields used to be sliced to 300/200/200
    # chars — the same silent-truncation habit that cut the synopsis to 6.5%
    # (docs/BRD.md §1). A full cast entry is ~500 tokens against a 1M window.
    for attr, label in (
        ("role", "Role"),
        ("description", "Description"),
        ("goals", "Goals"),
        ("arc", "Arc"),
    ):
        value = getattr(character, attr, None)
        if value:
            lines.append(f"{label}: {clean_for_llm(value)}")
    voice = getattr(character, "voice_characteristics", None)
    if voice:
        from app.planning.canon import _render_json_field

        rendered = _render_json_field(voice)
        if rendered:
            lines.append(f"Voice: {rendered}")
    samples = await _retrieve(character, beat_description, max_samples, chunk_types)
    if grounding is not None:
        grounding.extend(
            {
                "character_id": str(character.id),
                "chunk_id": s.get("id"),
                "score": s.get("score"),
            }
            for s in samples
        )
    if samples:
        lines.append("Voice samples (match this voice — cadence, diction, syntax):")
        # These samples ARE the voice grounding — the product's whole premise.
        # They used to be cut to 200 chars, which severed most of them
        # mid-sentence and taught the model half a cadence.
        lines.extend(f'- "{clean_for_llm(s["text"])}"' for s in samples)
    return "\n".join(lines)


async def _retrieve(
    character: Character,
    query: str,
    k: int,
    chunk_types: Optional[Iterable[str]],
) -> list[dict]:
    """Voice samples for one character. Retrieve across ALL chunk types unless a
    caller asks for specific ones: the ingest-time classifier under-labels, so a
    type filter alone can starve grounding to nothing. Dialogue ranks first."""
    store = get_chunk_store()
    common = dict(
        character_id=str(character.id),
        query=query,
        user_id=str(character.user_id) if character.user_id else None,
        book_id=str(character.book_id) if character.book_id else None,
    )
    if chunk_types:
        samples: list[dict] = []
        for t in chunk_types:
            samples.extend(await store.retrieve_similar(k=k, chunk_type=t, **common))
        samples.sort(key=lambda s: -(s.get("score") or 0))
        samples = samples[:k]
    else:
        samples = await store.retrieve_similar(k=k, **common)
    samples.sort(key=lambda s: s.get("chunk_type") != "dialogue")
    return samples


async def build_cast_context(
    characters: list[Character],
    beat_description: str,
    *,
    grounding: Optional[list] = None,
) -> str:
    """Fenced context blocks for every character in a beat.

    Each block is story material (bible text and voice samples can originate in
    an uploaded file), so it is fenced with `as_quoted_block`
    (docs/ADR-002-book-as-root.md §4).
    """
    blocks = []
    for character in characters:
        block = await build_character_context(
            character, character.name, beat_description, grounding=grounding
        )
        blocks.append(as_quoted_block(block, "character"))
    return "\n\n".join(b for b in blocks if b)
