"""API Gateway - Main entry point for Polyphony API"""

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
from services.shared.metrics import (
    http_requests_total,
    http_request_duration_seconds,
    http_request_size_bytes,
    http_response_size_bytes,
    initialize_service_metrics
)
from services.shared.logging_config import (
    setup_logging,
    log_request_start,
    log_request_end,
    log_error
)
from .routers import auth, manuscripts, scenes

# Initialize structured logging
logger = setup_logging("api-gateway", level=settings.LOG_LEVEL if hasattr(settings, 'LOG_LEVEL') else "INFO")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the application"""
    # Startup
    logger.info("Starting Polyphony API Gateway", extra_fields={"event": "service_startup"})

    # Initialize metrics
    start_time = time.time()
    update_uptime = initialize_service_metrics("api-gateway", "1.0.0", start_time)
    app.state.update_uptime = update_uptime

    # Check database connection
    db_healthy = await check_db_connection()
    if db_healthy:
        logger.info("Database connection established", extra_fields={"event": "database_connected"})
    else:
        logger.warning("Database connection failed - some features may not work", extra_fields={"event": "database_connection_failed"})

    yield

    # Shutdown
    logger.info("Shutting down Polyphony API Gateway", extra_fields={"event": "service_shutdown"})


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

# Compression middleware (P3-2 fix)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # Compress responses > 1KB

# CORS middleware - configurable via environment
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://frontend:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


# Request logging and metrics middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with timing and track metrics"""
    start_time = time.time()

    # Generate correlation ID for request tracing
    import uuid
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    logger.set_correlation_id(correlation_id)

    # Log request start
    log_request_start(logger, request.method, request.url.path)

    # Track request size
    request_size = int(request.headers.get("content-length", 0))
    if request_size > 0:
        http_request_size_bytes.labels(
            method=request.method,
            endpoint=request.url.path,
            service="api-gateway"
        ).observe(request_size)

    response = await call_next(request)

    # Calculate processing time
    process_time = time.time() - start_time
    duration_ms = process_time * 1000
    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-Correlation-ID"] = correlation_id

    # Track metrics
    http_requests_total.labels(
        method=request.method,
        endpoint=request.url.path,
        status_code=response.status_code,
        service="api-gateway"
    ).inc()

    http_request_duration_seconds.labels(
        method=request.method,
        endpoint=request.url.path,
        service="api-gateway"
    ).observe(process_time)

    # Track response size if available
    if "content-length" in response.headers:
        response_size = int(response.headers["content-length"])
        http_response_size_bytes.labels(
            method=request.method,
            endpoint=request.url.path,
            service="api-gateway"
        ).observe(response_size)

    # Log request completion
    log_request_end(logger, request.method, request.url.path, response.status_code, duration_ms)

    return response


# Global exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors with detailed messages"""
    logger.warning(
        f"Validation error on {request.method} {request.url.path}",
        extra_fields={
            "event": "validation_error",
            "errors": exc.errors(),
            "path": request.url.path,
            "method": request.method
        }
    )
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
    log_error(logger, exc, context={
        "path": request.url.path,
        "method": request.method,
        "event": "unhandled_exception"
    })
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
