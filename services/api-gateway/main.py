"""API Gateway - Main entry point for Polyphony API"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
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

# Rate limiting (P0-6 fix)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute", f"{settings.RATE_LIMIT_PER_HOUR}/hour"],
    storage_uri=settings.REDIS_URL if settings.REDIS_URL else "memory://"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Security headers middleware (SEC-1 fix)
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)


# Request size limit middleware (P1-5 fix)
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size: int = 10 * 1024 * 1024):  # 10MB default
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request, call_next):
        if request.headers.get("content-length"):
            content_length = int(request.headers["content-length"])
            if content_length > self.max_size:
                return JSONResponse(
                    {"error": "Request body too large", "max_size": self.max_size},
                    status_code=413
                )
        return await call_next(request)

app.add_middleware(RequestSizeLimitMiddleware, max_size=10 * 1024 * 1024)

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
