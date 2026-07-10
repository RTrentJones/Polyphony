"""Polyphony — one FastAPI application.

Consolidates the former api-gateway / orchestrator / character-agent /
document-parser services (docs/ADR-001). Serves the statically-exported
frontend at / and the API under /api/v1.
"""

from contextlib import asynccontextmanager
import os
import time
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.database import check_db_connection
from app.core.logging_config import (
    log_error,
    log_request_end,
    log_request_start,
    setup_logging,
)
from app.core.metrics import (
    http_request_duration_seconds,
    http_request_size_bytes,
    http_requests_total,
    http_response_size_bytes,
    initialize_service_metrics,
)

logger = setup_logging("app")

SERVICE_NAME = "polyphony"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info("Starting Polyphony", extra_fields={"event": "service_startup"})

    # Fail fast if the selected LLM provider has no key (production only —
    # tests and local tooling may run without one).
    from app.llm.client import LLMConfigurationError, get_llm_client

    try:
        client = get_llm_client()
        logger.info(
            f"LLM provider: {client.provider.id}",
            extra_fields={"event": "llm_provider_ready"},
        )
    except LLMConfigurationError as e:
        if settings.ENVIRONMENT == "production":
            raise
        logger.warning(
            f"LLM not configured: {e}",
            extra_fields={"event": "llm_not_configured"},
        )

    update_uptime = initialize_service_metrics(SERVICE_NAME, "1.0.0", time.time())
    app.state.update_uptime = update_uptime

    if await check_db_connection():
        logger.info(
            "Database connection established",
            extra_fields={"event": "database_connected"},
        )
    else:
        logger.warning(
            "Database connection failed - some features may not work",
            extra_fields={"event": "database_connection_failed"},
        )

    # First-boot admin bootstrap (no-op unless the users table is empty and
    # ADMIN_EMAIL/ADMIN_PASSWORD are set).
    from app.core.bootstrap import bootstrap_admin

    await bootstrap_admin()

    yield

    logger.info("Shutting down Polyphony", extra_fields={"event": "service_shutdown"})


app = FastAPI(
    title="Polyphony",
    version="1.0.0",
    description="Multi-character AI book-writing platform",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Rate limiting — in-process storage (one container; ADR-001 §4)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[
        f"{settings.RATE_LIMIT_PER_MINUTE}/minute",
        f"{settings.RATE_LIMIT_PER_HOUR}/hour",
    ],
    storage_uri="memory://",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size: int = 60 * 1024 * 1024):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request, call_next):
        if request.headers.get("content-length"):
            content_length = int(request.headers["content-length"])
            if content_length > self.max_size:
                return JSONResponse(
                    {"error": "Request body too large", "max_size": self.max_size},
                    status_code=413,
                )
        return await call_next(request)


# Sized above MAX_UPLOAD_SIZE so manuscript uploads pass the outer gate and
# get the parser's own size error instead of a blunt 413.
app.add_middleware(
    RequestSizeLimitMiddleware, max_size=settings.MAX_UPLOAD_SIZE + 10 * 1024 * 1024
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Request logging + Prometheus metrics with correlation IDs."""
    start_time = time.time()
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    logger.set_correlation_id(correlation_id)
    log_request_start(logger, request.method, request.url.path)

    request_size = int(request.headers.get("content-length", 0))
    if request_size > 0:
        http_request_size_bytes.labels(
            method=request.method, endpoint=request.url.path, service=SERVICE_NAME
        ).observe(request_size)

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    response.headers["X-Correlation-ID"] = correlation_id

    http_requests_total.labels(
        method=request.method,
        endpoint=request.url.path,
        status_code=response.status_code,
        service=SERVICE_NAME,
    ).inc()
    http_request_duration_seconds.labels(
        method=request.method, endpoint=request.url.path, service=SERVICE_NAME
    ).observe(process_time)
    if "content-length" in response.headers:
        http_response_size_bytes.labels(
            method=request.method, endpoint=request.url.path, service=SERVICE_NAME
        ).observe(int(response.headers["content-length"]))

    log_request_end(
        logger,
        request.method,
        request.url.path,
        response.status_code,
        process_time * 1000,
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        f"Validation error on {request.method} {request.url.path}",
        extra_fields={
            "event": "validation_error",
            "errors": exc.errors(),
            "path": request.url.path,
            "method": request.method,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "message": "Validation error - please check your request data",
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    log_error(
        logger,
        exc,
        context={
            "path": request.url.path,
            "method": request.method,
            "event": "unhandled_exception",
        },
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "message": "An unexpected error occurred. Please try again later.",
        },
    )


@app.get("/health")
async def health_check():
    """Health check: DB + vector store."""
    from app.rag.store import get_chunk_store

    db_healthy = await check_db_connection()
    qdrant_healthy = await get_chunk_store().healthy()
    return {
        "status": "healthy" if db_healthy else "degraded",
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "checks": {
            "database": "healthy" if db_healthy else "unhealthy",
            "qdrant": "healthy" if qdrant_healthy else "unhealthy",
        },
    }


@app.get("/__version")
async def version():
    """Artifact identity for Greenlight's SHA-gated verify."""
    return {"sha": os.getenv("GREENLIGHT_SHA", "")}


@app.get("/metrics")
async def metrics():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# API routers
from app.api import auth as auth_router  # noqa: E402
from app.api import characters as characters_router  # noqa: E402
from app.api import manuscripts as manuscripts_router  # noqa: E402
from app.api import scenes as scenes_router  # noqa: E402

app.include_router(
    auth_router.router,
    prefix="/api/v1/auth",
    tags=["Authentication"],
    responses={401: {"description": "Unauthorized"}},
)
app.include_router(
    manuscripts_router.router,
    prefix="/api/v1/manuscripts",
    tags=["Manuscripts"],
    responses={401: {"description": "Unauthorized"}},
)
app.include_router(
    scenes_router.router,
    prefix="/api/v1/scenes",
    tags=["Scenes"],
    responses={401: {"description": "Unauthorized"}},
)
app.include_router(
    characters_router.router,
    prefix="/api/v1/characters",
    tags=["Characters"],
    responses={401: {"description": "Unauthorized"}},
)

# Statically-exported frontend (present in the container image; absent in dev,
# where `next dev` serves it instead).
if os.path.isdir(settings.STATIC_DIR):
    app.mount(
        "/", StaticFiles(directory=settings.STATIC_DIR, html=True), name="frontend"
    )
else:

    @app.get("/")
    async def root():
        return {
            "service": "Polyphony",
            "version": "1.0.0",
            "status": "running",
            "docs": "/docs",
            "health": "/health",
        }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104
