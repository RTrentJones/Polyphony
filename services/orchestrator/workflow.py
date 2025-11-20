"""
LangGraph Workflow for Scene Generation

This module implements the multi-agent orchestration workflow using LangGraph.
It coordinates multiple character agents to generate coherent, character-driven scenes.
"""

from typing import TypedDict
from langgraph.graph import StateGraph, END
from uuid import uuid4
import httpx
from datetime import datetime
from groq import AsyncGroq

from services.shared.models import SceneRequest
from services.shared.config import settings
from services.shared.resilience import (
    CircuitBreaker,
    with_retry,
    CircuitBreakerError,
)
from services.shared.sanitization import sanitize_for_llm
from services.shared.logging_config import setup_logging, log_error, log_business_event

# Initialize logger for workflow
workflow_logger = setup_logging(
    "orchestrator.workflow",
    level=settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO",
)


# Singleton Groq client (P0-5 fix)
_groq_client: AsyncGroq | None = None

# Circuit breakers for external services (P0-4 fix)
character_agent_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60,
    expected_exception=(httpx.HTTPError, httpx.TimeoutException),
    name="character_agent",
)

groq_api_breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=30,
    expected_exception=Exception,
    name="groq_api",
)


def get_groq_client() -> AsyncGroq:
    """Get or create singleton Groq client"""
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(
            api_key=settings.GROQ_API_KEY, timeout=httpx.Timeout(60.0, connect=10.0)
        )
    return _groq_client


class SceneState(TypedDict):
    """State for scene generation workflow"""

    scene_request: dict
    beats: list[dict]  # Scene beats (sub-moments)
    current_beat_index: int
    current_dialogue: list[dict]  # Current beat's dialogue
    completed_beats: list[dict]  # Completed beats with dialogue
    error: str | None
    scene_id: str


async def plan_scene_beats(state: SceneState) -> SceneState:
    """
    Break down the scene into beats (smaller narrative moments)

    A beat is a single moment or exchange in the scene
    """
    scene_request = state["scene_request"]

    # Use LLM to plan beats (P0-5 fix: using singleton client)
    client = get_groq_client()

    characters_str = ", ".join(scene_request["characters"])

    # Sanitize inputs to prevent prompt injection (P2-7 fix)
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

    try:
        # Call LLM with retry and circuit breaker (P2-4 fix)
        @with_retry(max_attempts=3, base_delay=2.0, retryable_exceptions=(Exception,))
        async def call_groq_with_protection():
            return await groq_api_breaker.call(
                client.chat.completions.create,
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,
            )

        response = await call_groq_with_protection()

        beats_text = response.choices[0].message.content.strip()

        # Parse beats (simple parsing - could be improved)
        beats = []
        for line in beats_text.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                # Remove number/bullet
                beat_desc = line.split(".", 1)[-1].strip()
                if beat_desc:
                    beats.append(
                        {
                            "description": beat_desc,
                            "characters": scene_request[
                                "characters"
                            ],  # All characters for now
                            "dialogue": [],
                        }
                    )

        # If parsing failed, create default beats
        if not beats:
            beats = [
                {
                    "description": scene_request["scene_description"],
                    "characters": scene_request["characters"],
                    "dialogue": [],
                }
            ]

        state["beats"] = beats
        state["current_beat_index"] = 0
        state["completed_beats"] = []

    except Exception as e:
        state["error"] = f"Error planning beats: {str(e)}"
        state["beats"] = []

    return state


async def generate_beat_dialogue(state: SceneState) -> SceneState:
    """
    Generate dialogue for the current beat by coordinating character agents

    This implements a turn-based dialogue generation where characters
    respond to each other in sequence
    """
    beat_index = state["current_beat_index"]

    if beat_index >= len(state["beats"]):
        return state

    beat = state["beats"][beat_index]
    scene_request = state["scene_request"]

    # Number of dialogue turns to generate for this beat
    max_turns = min(len(beat["characters"]) * 2, 8)  # 2 turns per character, max 8

    dialogue_history = []

    try:
        for turn in range(max_turns):
            # Determine which character speaks next (rotate through characters)
            char_index = turn % len(beat["characters"])
            current_character = beat["characters"][char_index]
            other_characters = [c for c in beat["characters"] if c != current_character]

            # Call character agent to generate dialogue
            dialogue_response = await _call_character_agent(
                character_name=current_character,
                beat_description=beat["description"],
                scene_context={
                    "description": scene_request["scene_description"],
                    "setting": scene_request["setting"],
                    "emotional_tone": scene_request["emotional_tone"],
                },
                previous_dialogue=dialogue_history,
                other_characters=other_characters,
            )

            if dialogue_response:
                dialogue_history.append(
                    {
                        "character": current_character,
                        "dialogue": dialogue_response["dialogue"],
                        "action": dialogue_response.get("action", ""),
                        "turn": turn,
                    }
                )

        # Save completed beat
        beat["dialogue"] = dialogue_history
        state["completed_beats"].append(beat)
        state["current_beat_index"] += 1

    except Exception as e:
        state["error"] = f"Error generating dialogue: {str(e)}"

    return state


def should_continue_beats(state: SceneState) -> str:
    """Determine if we should generate more beats or finish"""
    if state.get("error"):
        return "finish"

    if state["current_beat_index"] < len(state["beats"]):
        return "continue"

    return "finish"


async def assemble_final_scene(state: SceneState) -> SceneState:
    """
    Assemble the final scene from completed beats

    Combines all dialogue, adds narrative connectors, formats output
    """
    from services.shared.database import get_async_session
    from services.shared.orm_models import Scene, SceneBeat
    from uuid import UUID

    scene_request = state["scene_request"]

    try:
        # Format the complete scene
        scene_text = f"# {scene_request.get('title', 'Generated Scene')}\n\n"
        scene_text += f"**Setting**: {scene_request['setting']}\n"
        scene_text += f"**Tone**: {scene_request['emotional_tone']}\n\n"
        scene_text += "---\n\n"

        total_word_count = 0

        for beat_idx, beat in enumerate(state["completed_beats"]):
            scene_text += f"## Beat {beat_idx + 1}\n\n"

            for dialogue_turn in beat["dialogue"]:
                char_name = dialogue_turn["character"]
                dialogue = dialogue_turn["dialogue"]
                action = dialogue_turn.get("action", "")

                if action:
                    scene_text += f"*{action}*\n\n"

                scene_text += f'**{char_name}**: "{dialogue}"\n\n'

                # Count words
                total_word_count += len(dialogue.split())

            scene_text += "\n"

        # Save to database
        async with get_async_session() as session:
            # Create Scene record
            scene = Scene(
                id=UUID(state["scene_id"]),
                manuscript_id=UUID(scene_request["manuscript_id"]),
                title=scene_request.get("title", "Generated Scene"),
                setting=scene_request["setting"],
                emotional_tone=scene_request["emotional_tone"],
                characters=scene_request["characters"],
                scene_description=scene_request["scene_description"],
                generated_content=scene_text,
                word_count=total_word_count,
                status="completed",
                created_at=datetime.utcnow(),
            )
            session.add(scene)

            # Create SceneBeat records
            for beat_idx, beat in enumerate(state["completed_beats"]):
                scene_beat = SceneBeat(
                    scene_id=scene.id,
                    beat_number=beat_idx,
                    description=beat["description"],
                    dialogue=beat["dialogue"],
                    created_at=datetime.utcnow(),
                )
                session.add(scene_beat)

            await session.commit()

        log_business_event(
            workflow_logger,
            "scene_saved_to_database",
            scene_id=state["scene_id"],
            beats_count=len(state["completed_beats"]),
            word_count=total_word_count,
        )

    except Exception as e:
        state["error"] = f"Error assembling scene: {str(e)}"
        log_error(
            workflow_logger,
            e,
            context={"scene_id": state["scene_id"], "event": "scene_assembly_failed"},
        )

    return state


async def _call_character_agent(
    character_name: str,
    beat_description: str,
    scene_context: dict,
    previous_dialogue: list[dict],
    other_characters: list[str],
) -> dict | None:
    """
    Call a character agent to generate dialogue with circuit breaker and retry (P0-4 fix)

    In a production deployment, this would route to specific character agent instances.
    For now, we'll use the character-agent service directly.
    """

    @with_retry(
        max_attempts=3,
        base_delay=1.0,
        retryable_exceptions=(httpx.HTTPError, httpx.TimeoutException),
    )
    async def make_request():
        agent_url = f"{settings.CHARACTER_AGENT_URL}/generate-dialogue"

        request_data = {
            "character": character_name,
            "beat_description": beat_description,
            "scene_context": scene_context,
            "previous_dialogue": previous_dialogue,
            "other_characters": other_characters,
            "emotional_state": scene_context.get("emotional_tone", "neutral"),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(agent_url, json=request_data)
            response.raise_for_status()
            return response.json()

    try:
        # Call with circuit breaker protection
        return await character_agent_breaker.call(make_request)

    except (httpx.HTTPError, httpx.TimeoutException, CircuitBreakerError) as e:
        # Log error with context
        workflow_logger.warning(
            f"Error calling character agent for {character_name}",
            extra_fields={
                "event": "character_agent_error",
                "character": character_name,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )

        # Fallback: return simple dialogue
        return {
            "character": character_name,
            "dialogue": "[I need a moment to collect my thoughts...]",
            "action": f"{character_name} pauses thoughtfully",
            "confidence_score": 0.0,
        }
    except Exception as e:
        log_error(
            workflow_logger,
            e,
            context={
                "event": "character_agent_unexpected_error",
                "character": character_name,
            },
        )
        return {
            "character": character_name,
            "dialogue": "...",
            "action": "",
            "confidence_score": 0.0,
        }


def create_scene_workflow() -> StateGraph:
    """
    Create the LangGraph workflow for scene generation

    Workflow:
    1. Plan beats (break scene into moments)
    2. Generate dialogue for each beat (coordinate character agents)
    3. Assemble final scene (combine all dialogue)
    """
    workflow = StateGraph(SceneState)

    # Add nodes
    workflow.add_node("plan_beats", plan_scene_beats)
    workflow.add_node("generate_dialogue", generate_beat_dialogue)
    workflow.add_node("assemble_scene", assemble_final_scene)

    # Define edges
    workflow.set_entry_point("plan_beats")

    workflow.add_edge("plan_beats", "generate_dialogue")

    workflow.add_conditional_edges(
        "generate_dialogue",
        should_continue_beats,
        {
            "continue": "generate_dialogue",  # Loop back for next beat
            "finish": "assemble_scene",
        },
    )

    workflow.add_edge("assemble_scene", END)

    return workflow.compile()


async def generate_scene(
    scene_request: SceneRequest, scene_id: str | None = None
) -> dict:
    """
    Main entry point for scene generation

    Args:
        scene_request: The scene generation request
        scene_id: Optional scene ID (will be generated if not provided)

    Returns:
        dict with scene_id, status, and other metadata
    """
    if scene_id is None:
        scene_id = str(uuid4())

    # Initialize state
    initial_state = SceneState(
        scene_request=scene_request.dict(),
        beats=[],
        current_beat_index=0,
        current_dialogue=[],
        completed_beats=[],
        error=None,
        scene_id=scene_id,
    )

    # Run workflow
    workflow = create_scene_workflow()

    try:
        final_state = await workflow.ainvoke(initial_state)

        if final_state.get("error"):
            return {
                "scene_id": scene_id,
                "status": "failed",
                "error": final_state["error"],
            }

        return {
            "scene_id": scene_id,
            "status": "completed",
            "beats_count": len(final_state["completed_beats"]),
            "total_dialogue_turns": sum(
                len(beat["dialogue"]) for beat in final_state["completed_beats"]
            ),
        }

    except Exception as e:
        return {"scene_id": scene_id, "status": "failed", "error": str(e)}
