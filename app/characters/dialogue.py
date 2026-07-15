"""Character-voice dialogue generation.

Was services/character-agent (one container per hardcoded character); now an
async function over any Character row — RAG-retrieve the character's voice
samples, generate the next line in that voice, score consistency.
"""

import hashlib
import json
from typing import Optional
from uuid import UUID

from cachetools import TTLCache

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.llm_text import clean_for_llm
from app.llm.client import get_llm_client
from app.rag.embeddings import cosine_similarity, get_embedder
from app.rag.store import get_chunk_store

logger = setup_logging("characters.dialogue")

# In-process dialogue cache (was Redis; ADR-001 §4)
_dialogue_cache: TTLCache = TTLCache(
    maxsize=settings.CACHE_MAX_ENTRIES, ttl=settings.CACHE_TTL_SECONDS
)


async def generate_dialogue(
    *,
    character_id: str,
    character_name: str,
    beat_description: str,
    scene_context: dict,
    emotional_state: str,
    other_characters: list[str],
    previous_dialogue: Optional[list[dict]] = None,
    user_id: Optional[UUID] = None,
) -> dict:
    """Generate one in-voice dialogue turn (plus action) for a character."""
    previous_dialogue = previous_dialogue or []

    cache_key = _cache_key(
        character_id, beat_description, emotional_state, previous_dialogue
    )
    if settings.CACHE_DIALOGUE and cache_key in _dialogue_cache:
        return _dialogue_cache[cache_key]

    store = get_chunk_store()
    # Retrieve across ALL chunk types, not dialogue-only: the ingest-time
    # classifier under-labels dialogue, so a dialogue-only filter starves this to
    # nothing (mirrors the fix in characters/context.py).
    similar = await store.retrieve_similar(
        character_id=character_id,
        query=scene_context.get("description", beat_description),
    )

    examples_text = (
        "\n".join(f"- \"{clean_for_llm(ex['text'])}\"" for ex in similar[:3])
        if similar
        else "No previous examples available."
    )
    safe_beat = clean_for_llm(beat_description)
    safe_setting = clean_for_llm(str(scene_context.get("setting", "Unknown")))

    prompt = f"""You are {character_name}, a character in this story.

Scene context: {safe_beat}
Setting: {safe_setting}
Your emotional state: {emotional_state}
Other characters present: {', '.join(other_characters)}

Here are examples of how {character_name} speaks:
{examples_text}

Previous dialogue in this scene:
{_format_previous_dialogue(previous_dialogue)}

Write {character_name}'s next line of dialogue. Match their unique voice and speech patterns from the examples.

Important:
- Stay in character
- Match the emotional tone: {emotional_state}
- Be natural and conversational
- Keep it concise (1-3 sentences)
- Do NOT include character name or quotes, just the dialogue

{character_name} says:"""

    client = get_llm_client()
    result = await client.generate(
        [{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=400,
        user_id=user_id,
        purpose="dialogue",
    )
    dialogue = result.text.strip('"').strip("'")

    action = await _generate_action(
        character_name, scene_context, dialogue, emotional_state, user_id
    )
    confidence = await _voice_consistency(dialogue, [ex["text"] for ex in similar])

    response = {
        "character": character_name,
        "dialogue": dialogue,
        "action": action,
        "confidence_score": confidence,
        "retrieved_examples": [ex["text"][:100] for ex in similar[:3]],
    }
    if settings.CACHE_DIALOGUE:
        _dialogue_cache[cache_key] = response
    return response


async def _generate_action(
    character_name: str,
    scene_context: dict,
    dialogue: str,
    emotional_state: str,
    user_id: Optional[UUID],
) -> str:
    """Generate a brief accompanying action (fast model)."""
    try:
        prompt = f"""Given this dialogue by {character_name}: "{dialogue}"
Emotional state: {emotional_state}
Scene: {scene_context.get('description', '')}

Write a brief action or body language for {character_name} (1 short sentence).
Do NOT include the character's name in the action.

Action:"""
        result = await get_llm_client().generate(
            [{"role": "user", "content": prompt}],
            fast=True,
            temperature=0.7,
            max_tokens=150,
            user_id=user_id,
            purpose="action",
        )
        return result.text
    except Exception as e:
        logger.warning(
            f"Action generation failed: {e}",
            extra_fields={"event": "action_generation_failed"},
        )
        return ""


async def _voice_consistency(generated: str, examples: list[str]) -> float:
    """Semantic similarity between generated dialogue and the voice examples."""
    if not examples:
        return 0.5
    try:
        embedder = get_embedder()
        vectors = await embedder.aencode([generated, *examples])
        generated_vec, example_vecs = vectors[0], vectors[1:]
        scores = [cosine_similarity(generated_vec, ev) for ev in example_vecs]
        return max(0.0, min(1.0, sum(scores) / len(scores)))
    except Exception as e:
        logger.warning(
            f"Voice consistency scoring failed: {e}",
            extra_fields={"event": "voice_consistency_failed"},
        )
        return 0.5


def _format_previous_dialogue(dialogue_list: list[dict]) -> str:
    if not dialogue_list:
        return "This is the start of the scene."
    return "\n".join(
        f"{turn.get('character', 'Unknown')}: \"{turn.get('dialogue', '')}\""
        for turn in dialogue_list[-5:]
    )


def _cache_key(
    character_id: str,
    beat_description: str,
    emotional_state: str,
    previous_dialogue: list[dict],
) -> str:
    payload = json.dumps(
        [character_id, beat_description, emotional_state, previous_dialogue],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def clear_dialogue_cache() -> None:
    """Test hook."""
    _dialogue_cache.clear()
