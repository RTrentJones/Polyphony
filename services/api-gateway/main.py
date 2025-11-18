"""API Gateway - Main entry point for Polyphony API"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from services.shared.config import settings


app = FastAPI(
    title="Polyphony API Gateway",
    version="1.0.0",
    description="Central API gateway for Polyphony creative writing platform"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Polyphony API Gateway",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "api-gateway",
        "version": "1.0.0"
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
    from fastapi import Response

    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )


# TODO: Add routers
# from .routers import auth, manuscripts, scenes, characters
# app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
# app.include_router(manuscripts.router, prefix="/api/v1/manuscripts", tags=["manuscripts"])
# app.include_router(scenes.router, prefix="/api/v1/scenes", tags=["scenes"])
# app.include_router(characters.router, prefix="/api/v1/characters", tags=["characters"])


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVICE_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
