"""Orchestrator Service - Multi-Agent Scene Generation"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import os
from uuid import uuid4
from datetime import datetime
import time

from services.shared.config import settings
from services.shared.models import SceneRequest
from services.shared.database import get_async_session
from services.shared.orm_models import Scene
from services.shared.logging_config import (
    setup_logging,
    log_business_event,
    log_error
)
from services.shared.metrics import (
    scenes_generated_total,
    scene_generation_duration_seconds,
    initialize_service_metrics
)
from .workflow import generate_scene

# Initialize structured logging
logger = setup_logging("orchestrator", level=settings.LOG_LEVEL if hasattr(settings, 'LOG_LEVEL') else "INFO")


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the orchestrator service"""
    # Startup
    logger.info("Starting Polyphony Orchestrator Service", extra_fields={"event": "service_startup"})

    # Initialize metrics
    start_time = time.time()
    update_uptime = initialize_service_metrics("orchestrator", "1.0.0", start_time)
    app.state.update_uptime = update_uptime

    yield

    # Shutdown
    logger.info("Shutting down Polyphony Orchestrator Service", extra_fields={"event": "service_shutdown"})


app = FastAPI(
    title="Polyphony Orchestrator",
    version="1.0.0",
    description="Multi-agent narrative orchestration service with LangGraph",
    lifespan=lifespan
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
    start_time = time.time()

    try:
        log_business_event(
            logger,
            "scene_generation_started",
            scene_id=scene_id,
            manuscript_id=str(request.manuscript_id),
            characters=request.characters,
            setting=request.setting
        )

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

        # Track metrics
        scenes_generated_total.labels(service="orchestrator", status="started").inc()

        # Launch workflow in background
        async def generate_with_metrics():
            """Wrapper to track generation metrics"""
            workflow_start = time.time()
            try:
                result = await generate_scene(request, scene_id)
                duration = time.time() - workflow_start

                # Track successful completion
                scenes_generated_total.labels(service="orchestrator", status="completed").inc()
                scene_generation_duration_seconds.labels(service="orchestrator").observe(duration)

                log_business_event(
                    logger,
                    "scene_generation_completed",
                    scene_id=scene_id,
                    duration_seconds=duration,
                    status=result.get('status')
                )

                return result
            except Exception as e:
                duration = time.time() - workflow_start

                # Track failure
                scenes_generated_total.labels(service="orchestrator", status="failed").inc()
                scene_generation_duration_seconds.labels(service="orchestrator").observe(duration)

                log_error(logger, e, context={
                    "scene_id": scene_id,
                    "event": "scene_generation_failed",
                    "duration_seconds": duration
                })
                raise

        background_tasks.add_task(generate_with_metrics)

        return {
            "status": "processing",
            "scene_id": scene_id,
            "message": "Scene generation started. Poll /scene/{scene_id} for status."
        }

    except Exception as e:
        log_error(logger, e, context={
            "scene_id": scene_id,
            "event": "scene_orchestration_error"
        })
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
