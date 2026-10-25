"""Character-voice dialogue generation: the voice workbench's "Test dialogue".

One caller (`app/api/characters.test_dialogue`). The samples come from the
scene path (`context.character_samples`) and print through its renderer
(`context.render_character`), so the author tests the voice their scenes are
written from (SPEC-17, W3-5).
"""

from typing import Optional
from uuid import UUID

from app.characters.context import (
    SceneScope,
    character_samples,
    grounding_entries,
    render_character,
)
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import setup_logging
from app.core.orm_models import Character
from app.llm.client import get_llm_client
from app.rag.embeddings import cosine_similarity, get_embedder

logger = setup_logging("characters.dialogue")


async def generate_dialogue(
    *,
    character: Character,
    prompt: str,
    context: str,
    scope: SceneScope,
    user_id: Optional[UUID] = None,
) -> dict:
    """One in-voice line (plus an action) for `character`: `prompt` is the
    moment to answer, `context` the author's optional scene note."""
    name = character.name
    # Score >= 0: a voice probe's own best lines score low against it (SPEC-55).
    words, movement = await character_samples(
        character, prompt, 3, scope=scope, threshold=0, own=(own := [])
    )
    block = as_quoted_block(
        render_character(name, words, movement, (), own), "character"
    )
    words += own  # similarity and grounding read every sample shown
    note = clean_for_llm(context)
    scene = f"Scene context: {note}\n" if note else ""
    body = f"""{STORY_MATERIAL_NOTICE}

You are {name}, a character in this story.

{block}

{scene}The moment: {clean_for_llm(prompt)}

Write {name}'s next line of dialogue. Match the cadence, diction and syntax of their voice samples.

Important:
- Stay in character
- Be natural and conversational
- Keep it to one to three sentences
- Do NOT include character name or quotes, just the dialogue

{name} says:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": body}],
        temperature=0.9,
        max_tokens=400,
        user_id=user_id,
        purpose="dialogue",
    )
    dialogue = result.text.strip('"').strip("'")
    return {
        "character": name,
        "dialogue": dialogue,
        "action": await _generate_action(name, block, dialogue, user_id),
        "confidence_score": await _voice_consistency(
            dialogue, [s["text"] for s in words]
        ),
        # Recorded like a scene's (R5.3), so the workbench shows what grounded it.
        "grounding": grounding_entries(character, [*words, *movement]),
    }


async def _generate_action(
    character_name: str, block: str, dialogue: str, user_id: Optional[UUID]
) -> str:
    """A brief accompanying action (fast model), from the same character block."""
    try:
        prompt = f"""{STORY_MATERIAL_NOTICE}

{block}

Given this dialogue by {character_name}: "{dialogue}"

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
