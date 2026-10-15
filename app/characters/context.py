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

import re
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.llm_text import as_quoted_block, clean_for_llm
from app.core.orm_models import Character
from app.rag.store import get_chunk_store

# A grounding record keeps a bounded excerpt of each sample's text as it was at
# generation time, so a sample deleted since still shows what grounded the
# scene (R5.3). Bounded: the record lives in Scene.evaluation_scores and is
# provenance, not a second copy of the voice index.
GROUNDING_EXCERPT_CHARS = 300
NO_SAMPLES_REASON = "no indexed voice samples"
CONTEXT_ONLY_REASON = "context only"


def grounding_excerpt(text_value: Optional[str]) -> Optional[str]:
    """The first ~300 chars of a sample, cut at a word boundary when possible."""
    if not text_value:
        return None
    text_value = text_value.strip()
    if len(text_value) <= GROUNDING_EXCERPT_CHARS:
        return text_value
    cut = text_value[:GROUNDING_EXCERPT_CHARS]
    space = cut.rfind(" ")
    if space > GROUNDING_EXCERPT_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def unsampled_entry(character: Character, reason: str) -> dict:
    """A grounding entry for a character no sample grounded, and why."""
    return {
        "character_id": str(character.id),
        "chunk_id": None,
        "score": None,
        "reason": reason,
    }


def grounding_entries(character: Character, samples: list[dict]) -> list[dict]:
    """One character's grounding-record entries for one beat.

    A character with no samples still gets an entry (no chunk, with a reason),
    so the grounding view lists every character in the scene instead of
    silently omitting the ones written without voice samples.
    """
    if not samples:
        return [unsampled_entry(character, NO_SAMPLES_REASON)]
    return [
        {
            "character_id": str(character.id),
            "chunk_id": s.get("id"),
            "score": s.get("score"),
            "excerpt": grounding_excerpt(s.get("text")),
        }
        for s in samples
    ]


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
    cast: Optional[Iterable] = None,
) -> str:
    """One character's context block: bible summary, then their own words
    ("Voice samples") and how they move ("How they move"), retrieved separately
    (SPEC-17).

    `grounding`, when given, collects the retrieved chunks (id, score and a
    bounded text excerpt) so the caller can record which samples grounded this
    generation (R5.3); a character with none gets a reason entry instead.
    `cast` is the rest of the scene's cast: a narration sample that centres one
    of them rather than this character is left out. `chunk_types`, when given,
    limits both blocks to those types.
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
    if max_samples <= 0:
        return "\n".join(lines)
    words, movement = await _voice_and_movement(
        character,
        beat_description,
        max_samples,
        min(_MOVEMENT_SAMPLES, max_samples),
        cast=cast,
        chunk_types=chunk_types,
    )
    if grounding is not None:
        grounding.extend(grounding_entries(character, [*words, *movement]))
    if words:
        lines.append(VOICE_SAMPLES_LABEL)
        # These samples ARE the voice grounding — the product's whole premise.
        # They used to be cut to 200 chars, which severed most of them
        # mid-sentence and taught the model half a cadence.
        lines.extend(f'- "{clean_for_llm(s["text"])}"' for s in words)
    if movement:
        lines.append(MOVEMENT_LABEL)
        lines.extend(f"- {clean_for_llm(s['text'])}" for s in movement)
    return "\n".join(lines)


# The prompt labels (SPEC-17). Only the character's own words sit under the
# voice label — dialogue the attributor credited to them, or first-person
# narration of their own section (SPEC-09). Everything else is movement.
VOICE_SAMPLES_LABEL = (
    "Voice samples (their own words — match cadence, diction, syntax):"
)
MOVEMENT_LABEL = "How they move:"
_MOVEMENT_SAMPLES = 3
# Ensemble agents are grounded on how they SPEAK and how they MOVE separately
# (BRD R6.2): one mixed retrieval lets dialogue crowd action out. The ingest
# classifier under-labels, so a movement block with fewer than this many hits
# falls back to untyped retrieval rather than leaving the agent ungrounded. The
# fallback NEVER feeds the voice block: narration is not a character's words.
_MIN_TYPED_HITS = 2
_ACTION_TYPES = ("action", "description", "thought")
_DIALOGUE = "dialogue"


def _own_narration(sample: dict) -> bool:
    """First-person narration indexed for its narrator (SPEC-09): their words."""
    return str(sample.get("source") or "").startswith("narration_")


def _forms(member) -> list[str]:
    """A cast member's name forms — full name, aliases, and each significant
    name token (a book calls "Zara Okafor" "Zara")."""
    from app.parsing.names import significant

    if isinstance(member, dict):
        name, aliases = member.get("name") or "", member.get("aliases") or []
    else:
        name = getattr(member, "name", "") or ""
        aliases = getattr(member, "aliases", None) or []
    forms = [name, *aliases]
    forms += [t for f in list(forms) for t in significant(f) if len(t) > 2]
    return [f for f in forms if f and f.strip()]


def _names_in(text: str, form: str) -> bool:
    return bool(
        re.search(
            r"(?<!\w)" + r"\s+".join(map(re.escape, form.split())) + r"(?!\w)",
            text,
            re.IGNORECASE,
        )
    )


_LEAD_NAME = re.compile(
    r"^\W*((?:(?:Mr|Mrs|Ms|Dr|Prof|St)\.\s+)?[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})"
)


def _centres(
    text: str, name: str, cast: Optional[Iterable] = None, aliases=None
) -> bool:
    """Does this narration centre the character? Yes when they are the subject
    of its first sentence, or when no one else from the cast — and no other
    proper name — appears in it. "Milo Voss read the appendices. Zara Okafor
    watched him read them." centres Milo, not Zara (SPEC-17)."""
    from app.parsing.names import match

    own = _forms({"name": name, "aliases": aliases or []})
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    lead = _LEAD_NAME.match(first)
    if lead:
        words = lead.group(1).split()
        # "Then Milo …": try the name with leading words dropped as well.
        if any(match(" ".join(words[i:]), f) for i in range(len(words)) for f in own):
            return True
    own_low = {f.lower() for f in own}
    for member in cast or []:
        m_name = member.get("name") if isinstance(member, dict) else member.name
        if m_name == name:
            continue
        if any(f.lower() not in own_low and _names_in(text, f) for f in _forms(member)):
            return False
    # A proper name that is not theirs, past a sentence's first word, is
    # someone (or somewhere) else the passage may be about.
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        for word in re.findall(r"(?<=\s)[A-Z][\w'-]*", sentence):
            if word != "I" and word.lower() not in own_low:
                return False
    return True


async def _voice_and_movement(
    character: Character,
    query: str,
    k_voice: int,
    k_move: int,
    *,
    cast: Optional[Iterable] = None,
    chunk_types: Optional[Iterable[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """(their own words, how they move) for one character, retrieved separately.

    Voice is dialogue chunks plus first-person narration of their own section;
    it never falls back to untyped retrieval. Movement is action / description /
    thought, falling back to untyped retrieval (minus dialogue) when the
    classifier under-labelled, and keeps only narration that centres them.
    """
    allowed = set(chunk_types) if chunk_types else None
    voice_types = [_DIALOGUE] if allowed is None or _DIALOGUE in allowed else []
    move_types = [t for t in _ACTION_TYPES if allowed is None or t in allowed]

    words = (
        [
            s
            for s in await _retrieve(character, query, k_voice, voice_types)
            if (s.get("chunk_type") or _DIALOGUE) == _DIALOGUE
        ]
        if voice_types
        else []
    )
    seen = {s.get("id") for s in words}

    def fresh(samples: list[dict]) -> list[dict]:
        out = []
        for s in samples:
            if s.get("id") in seen or s.get("chunk_type") == _DIALOGUE:
                continue
            if allowed is not None and s.get("chunk_type") not in allowed:
                continue
            seen.add(s.get("id"))
            out.append(s)
        return out

    body = (
        fresh(await _retrieve(character, query, k_move, move_types))
        if move_types
        else []
    )
    if move_types and len(body) < _MIN_TYPED_HITS:
        body += fresh(await _retrieve(character, query, k_move * 2, None))
    aliases = getattr(character, "aliases", None) or []
    name = getattr(character, "name", "") or ""
    # First-person narration of their own section is their voice, not movement.
    words += [s for s in body if _own_narration(s)][: max(0, k_voice - len(words))]
    body = [
        s
        for s in body
        if not _own_narration(s)
        and (
            not str(s.get("source") or "").startswith("paragraph_")
            or _centres(s.get("text") or "", name, cast, aliases)
        )
    ][:k_move]
    return words, body


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
        unique: dict = {}
        for s in samples:  # a store may return one chunk under several types
            unique.setdefault(s.get("id") or id(s), s)
        samples = sorted(unique.values(), key=lambda s: -(s.get("score") or 0))[:k]
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
            character,
            character.name,
            beat_description,
            grounding=grounding,
            cast=characters,
        )
        blocks.append(as_quoted_block(block, "character"))
    return "\n\n".join(b for b in blocks if b)


async def build_agent_context(
    character: Optional[Character],
    name: str,
    beat_description: str,
    *,
    k: int = 3,
    grounding: Optional[list] = None,
) -> str:
    """A character agent's fenced context: bible, then <voice> (their own words)
    and <physicality> (action/description) samples retrieved separately
    (SPEC-14, SPEC-17). <voice> never falls back to narration."""
    bio = await build_character_context(character, name, beat_description, 0)
    blocks = [as_quoted_block(bio, "character")]
    if character is None:
        return "\n\n".join(b for b in blocks if b)

    voice, body = await _voice_and_movement(character, beat_description, k, k)
    if grounding is not None:
        grounding.extend(grounding_entries(character, [*voice, *body]))
    if voice:
        blocks.append(
            as_quoted_block(
                "\n".join(f'- "{clean_for_llm(s["text"])}"' for s in voice), "voice"
            )
        )
    if body:
        blocks.append(
            as_quoted_block(
                "\n".join(f"- {clean_for_llm(s['text'])}" for s in body),
                "physicality",
            )
        )
    return "\n\n".join(b for b in blocks if b)
