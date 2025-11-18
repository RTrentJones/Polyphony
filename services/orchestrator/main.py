"""Orchestrator Service - Multi-Agent Scene Generation"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import os
from uuid import uuid4
from datetime import datetime

from services.shared.config import settings
from services.shared.models import SceneRequest
from services.shared.database import get_async_session
from services.shared.orm_models import Scene
from .workflow import generate_scene


app = FastAPI(
    title="Polyphony Orchestrator",
    version="1.0.0",
    description="Multi-agent narrative orchestration service with LangGraph"
)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "orchestrator",
        "version": "1.0.0",
        "workflow": "langgraph"
    }


@app.post("/orchestrate")
async def orchestrate_scene(request: SceneRequest, background_tasks: BackgroundTasks):
    """
    Orchestrate scene generation across multiple character agents

    This endpoint:
    1. Creates a scene record in the database
    2. Launches the LangGraph workflow in the background
    3. Returns immediately with scene_id for polling

    The workflow:
    - Plans scene beats (narrative moments)
    - Generates dialogue for each beat using character agents
    - Assembles the final scene
    - Saves to database
    """
    scene_id = str(uuid4())

    try:
        # Create initial scene record
        async with get_async_session() as session:
            scene = Scene(
                id=scene_id,
                manuscript_id=request.manuscript_id,
                title=f"Scene: {request.scene_description[:50]}...",
                setting=request.setting,
                emotional_tone=request.emotional_tone,
                characters=request.characters,
                scene_description=request.scene_description,
                generated_content="",
                word_count=0,
                status='processing',
                created_at=datetime.utcnow()
            )
            session.add(scene)
            await session.commit()

        # Launch workflow in background
        background_tasks.add_task(generate_scene, request, scene_id)

        return {
            "status": "processing",
            "scene_id": scene_id,
            "message": "Scene generation started. Poll /scene/{scene_id} for status."
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error starting scene generation: {str(e)}"
        )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
    from fastapi import Response

    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVICE_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
