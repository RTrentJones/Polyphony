"""Character Agent Service - Main API"""

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import REGISTRY
import os
import time
import redis.asyncio as redis
from groq import AsyncGroq
from sentence_transformers import util
import json

from services.shared.config import settings
from services.shared.models import DialogueRequest, DialogueResponse
from .rag_system import CharacterRAG


# Get character-specific config from environment
CHARACTER_NAME = os.getenv("CHARACTER_NAME", "Unknown")
CHARACTER_ID = os.getenv("CHARACTER_ID", "unknown")

app = FastAPI(
    title=f"Polyphony Character Agent - {CHARACTER_NAME}",
    version="1.0.0",
    description=f"Character-specific dialogue generation for {CHARACTER_NAME}",
)

# Initialize clients
groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
redis_client = None  # Initialized in startup
rag_system = CharacterRAG(
    character_id=CHARACTER_ID,
    character_name=CHARACTER_NAME,
    qdrant_url=settings.QDRANT_URL,
    embedding_model_name=settings.EMBEDDING_MODEL,
)

# Prometheus metrics
dialogue_requests = Counter(
    "dialogue_requests_total",
    "Total dialogue generation requests",
    ["character", "status"],
)
dialogue_duration = Histogram(
    "dialogue_generation_duration_seconds", "Time to generate dialogue", ["character"]
)
rag_retrieval_duration = Histogram(
    "rag_retrieval_duration_seconds", "Time to retrieve from RAG", ["character"]
)


@app.on_event("startup")
async def startup():
    """Initialize on startup"""
    global redis_client

    # Initialize Redis
    try:
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        await redis_client.ping()
        print(f"Connected to Redis: {settings.REDIS_URL}")
    except Exception as e:
        print(f"Warning: Could not connect to Redis: {e}")
        redis_client = None

    # Ensure Qdrant collection exists
    try:
        await rag_system.create_collection()
        print(f"Character RAG system ready for {CHARACTER_NAME}")
    except Exception as e:
        print(f"Warning: Could not initialize RAG system: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    if redis_client:
        await redis_client.close()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    stats = await rag_system.get_character_statistics()

    return {
        "status": "healthy",
        "service": "character-agent",
        "character": CHARACTER_NAME,
        "character_id": CHARACTER_ID,
        "collection_size": stats.get("total_chunks", 0),
        "rag_ready": stats.get("total_chunks", 0) > 0,
    }


@app.post("/generate-dialogue", response_model=DialogueResponse)
async def generate_dialogue(request: DialogueRequest):
    """
    Generate dialogue for this character

    This endpoint:
    1. Retrieves similar past dialogue from RAG
    2. Constructs prompt with examples
    3. Generates new dialogue via Groq
    4. Calculates voice consistency score
    5. Caches result in Redis
    """
    start_time = time.time()

    try:
        # 1. Check cache
        cache_key = f"dialogue:{CHARACTER_ID}:{hash(str(request.dict()))}"
        if redis_client and settings.CACHE_DIALOGUE:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    dialogue_requests.labels(
                        character=CHARACTER_NAME, status="cache_hit"
                    ).inc()
                    return DialogueResponse(**json.loads(cached))
            except Exception as e:
                print(f"Cache read error: {e}")

        # 2. Retrieve similar past dialogue from RAG
        retrieval_start = time.time()
        similar_examples = await rag_system.retrieve_similar_dialogue(
            query=request.scene_context.get("description", request.beat_description),
            k=settings.RAG_TOP_K,
            chunk_type="dialogue",
            score_threshold=settings.RAG_SCORE_THRESHOLD,
        )
        rag_retrieval_duration.labels(character=CHARACTER_NAME).observe(
            time.time() - retrieval_start
        )

        # 3. Build prompt with retrieved examples
        examples_text = (
            "\n".join([f"- \"{ex['text'][:200]}\"" for ex in similar_examples[:3]])
            if similar_examples
            else "No previous examples available."
        )

        previous_dialogue_text = _format_previous_dialogue(request.previous_dialogue)

        prompt = f"""You are {CHARACTER_NAME}, a character in this story.

Scene context: {request.beat_description}
Setting: {request.scene_context.get('setting', 'Unknown')}
Your emotional state: {request.emotional_state}
Other characters present: {', '.join(request.other_characters)}

Here are examples of how {CHARACTER_NAME} speaks:
{examples_text}

Previous dialogue in this scene:
{previous_dialogue_text}

Write {CHARACTER_NAME}'s next line of dialogue. Match their unique voice and speech patterns from the examples.

Important:
- Stay in character
- Match the emotional tone: {request.emotional_state}
- Be natural and conversational
- Keep it concise (1-3 sentences)
- Do NOT include character name or quotes, just the dialogue

{CHARACTER_NAME} says:"""

        # 4. Generate with Groq
        response = await groq_client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=200,
        )

        dialogue = response.choices[0].message.content.strip()

        # Clean up dialogue (remove quotes if present)
        dialogue = dialogue.strip('"').strip("'")

        # 5. Generate accompanying action
        action = await _generate_action(
            request.scene_context, dialogue, request.emotional_state
        )

        # 6. Calculate voice consistency
        confidence = (
            _calculate_voice_consistency(
                dialogue, [ex["text"] for ex in similar_examples]
            )
            if similar_examples
            else 0.5
        )

        # 7. Create response
        result = DialogueResponse(
            character=CHARACTER_NAME,
            dialogue=dialogue,
            action=action,
            confidence_score=confidence,
            retrieved_examples=[ex["text"][:100] for ex in similar_examples[:3]],
        )

        # 8. Cache result
        if redis_client and settings.CACHE_DIALOGUE:
            try:
                await redis_client.set(
                    cache_key, result.json(), ex=settings.CACHE_TTL_SECONDS
                )
            except Exception as e:
                print(f"Cache write error: {e}")

        # 9. Metrics
        dialogue_requests.labels(character=CHARACTER_NAME, status="success").inc()

        return result

    except Exception as e:
        dialogue_requests.labels(character=CHARACTER_NAME, status="error").inc()
        raise HTTPException(
            status_code=500, detail=f"Error generating dialogue: {str(e)}"
        )

    finally:
        dialogue_duration.labels(character=CHARACTER_NAME).observe(
            time.time() - start_time
        )


async def _generate_action(
    scene_context: dict, dialogue: str, emotional_state: str
) -> str:
    """Generate action to accompany dialogue"""
    try:
        prompt = f"""Given this dialogue by {CHARACTER_NAME}: "{dialogue}"
Emotional state: {emotional_state}
Scene: {scene_context.get('description', '')}

Write a brief action or body language for {CHARACTER_NAME} (1 short sentence).
Do NOT include the character's name in the action.

Action:"""

        response = await groq_client.chat.completions.create(
            model=settings.GROQ_MODEL_FAST,  # Use faster model for actions
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=50,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"Error generating action: {e}")
        return ""


def _calculate_voice_consistency(generated: str, examples: list[str]) -> float:
    """
    Calculate how well generated dialogue matches character voice

    Uses semantic similarity between generated text and examples
    """
    if not examples:
        return 0.5  # No examples to compare

    try:
        # Calculate semantic similarity
        gen_embedding = rag_system.embedding_model.encode(generated)
        example_embeddings = rag_system.embedding_model.encode(examples)

        similarities = util.cos_sim(gen_embedding, example_embeddings)
        avg_similarity = float(similarities.mean())

        # Normalize to 0-1 range (cosine similarity is already -1 to 1, but typically 0-1 for similar texts)
        return max(0.0, min(1.0, avg_similarity))

    except Exception as e:
        print(f"Error calculating voice consistency: {e}")
        return 0.5


def _format_previous_dialogue(dialogue_list: list[dict[str, str]]) -> str:
    """Format previous dialogue for context"""
    if not dialogue_list:
        return "This is the start of the scene."

    formatted = []
    for turn in dialogue_list[-5:]:  # Last 5 turns
        formatted.append(
            f"{turn.get('character', 'Unknown')}: \"{turn.get('dialogue', '')}\""
        )

    return "\n".join(formatted)


@app.post("/index-content")
async def index_content(chunks: list[dict]):
    """
    Index character content into RAG system

    Args:
        chunks: List of content chunks with text, chunk_type, source_location
    """
    try:
        indexed_count = await rag_system.index_character_content(chunks)

        return {
            "status": "success",
            "character": CHARACTER_NAME,
            "indexed_count": indexed_count,
            "total_chunks": len(chunks),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error indexing content: {str(e)}")


@app.get("/statistics")
async def get_statistics():
    """Get character RAG statistics"""
    stats = await rag_system.get_character_statistics()
    return stats


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from fastapi import Response

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("SERVICE_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104
