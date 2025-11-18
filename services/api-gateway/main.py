"""API Gateway - Main entry point for Polyphony API"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import os
import time

from services.shared.config import settings
from services.shared.database import check_db_connection, create_tables
from .routers import auth, manuscripts, scenes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the application"""
    # Startup
    print("🚀 Starting Polyphony API Gateway...")

    # Check database connection
    db_healthy = await check_db_connection()
    if db_healthy:
        print("✅ Database connection established")
    else:
        print("⚠️  Database connection failed - some features may not work")

    yield

    # Shutdown
    print("👋 Shutting down Polyphony API Gateway...")


app = FastAPI(
    title="Polyphony API Gateway",
    version="1.0.0",
    description="Central API gateway for Polyphony creative writing platform",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# CORS middleware - configurable via environment
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://frontend:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with timing"""
    start_time = time.time()

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)

    print(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")

    return response


# Global exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors with detailed messages"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "body": exc.body,
            "message": "Validation error - please check your request data"
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler"""
    print(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "message": "An unexpected error occurred. Please try again later."
        }
    )


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Polyphony API Gateway",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "auth": "/api/v1/auth",
            "manuscripts": "/api/v1/manuscripts",
            "scenes": "/api/v1/scenes (coming soon)"
        }
    }


@app.get("/health")
async def health_check():
    """Comprehensive health check endpoint"""
    db_healthy = await check_db_connection()

    return {
        "status": "healthy" if db_healthy else "degraded",
        "service": "api-gateway",
        "version": "1.0.0",
        "checks": {
            "database": "healthy" if db_healthy else "unhealthy"
        }
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


# Include routers
app.include_router(
    auth.router,
    prefix="/api/v1/auth",
    tags=["Authentication"],
    responses={401: {"description": "Unauthorized"}}
)

app.include_router(
    manuscripts.router,
    prefix="/api/v1/manuscripts",
    tags=["Manuscripts"],
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Manuscript not found"}
    }
)

app.include_router(
    scenes.router,
    prefix="/api/v1/scenes",
    tags=["Scenes"],
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Scene not found"}
    }
)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVICE_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
