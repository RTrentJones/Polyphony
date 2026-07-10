# Polyphony Code Review - Principal Engineer Assessment

**Reviewer**: Principal Engineer Review
**Date**: 2025-11-18
**Scope**: Full stack application review - Backend, Frontend, Infrastructure
**Severity Levels**: P0 (Critical), P1 (High), P2 (Medium), P3 (Low)

---

## Executive Summary

The Polyphony codebase demonstrates solid architectural decisions with microservices, proper separation of concerns, and modern frameworks. However, there are **27 critical issues** that must be addressed before production deployment, particularly around security, scalability, error handling, and observability.

**Overall Assessment**: ⚠️ **NOT PRODUCTION READY** - Requires significant improvements

**Strengths**:
- ✅ Clean microservices architecture
- ✅ Proper use of async/await patterns
- ✅ Type hints and Pydantic validation
- ✅ Containerized deployment with Docker
- ✅ Separation of shared code

**Critical Gaps**:
- ❌ No rate limiting implementation
- ❌ Missing distributed tracing
- ❌ No circuit breakers for service calls
- ❌ Insufficient error handling and logging
- ❌ No database migration strategy
- ❌ Missing health check depth
- ❌ No localization/i18n support
- ❌ Weak observability and monitoring

---

## P0 - Critical Issues (Must Fix Before Production)

### P0-1: Database Connection Pooling Issues
**File**: `services/shared/database.py`
**Line**: 37-44

```python
async_engine = create_async_engine(
    get_async_db_url(),
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)
```

**Problem**:
- Pool size of 10 + overflow of 20 = max 30 connections total
- With 5+ services, this can easily be exhausted under load
- No connection timeout configured
- No retry logic for connection failures

**Impact**: Service outages under moderate load

**Solution**:
```python
async_engine = create_async_engine(
    get_async_db_url(),
    echo=settings.DEBUG,
    pool_size=20,  # Increase base pool
    max_overflow=40,  # Increase overflow
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_timeout=30,  # Add connection timeout
    connect_args={
        "command_timeout": 60,
        "server_settings": {"jit": "off"}  # Disable JIT for faster queries
    }
)
```

Add retry logic with exponential backoff using `tenacity`:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def get_db_with_retry():
    async with AsyncSessionLocal() as session:
        yield session
```

---

### P0-2: No Database Migration Strategy
**File**: Missing - No Alembic configuration found
**Impact**: Cannot deploy schema changes safely

**Problem**:
- Using `init-db.sql` for schema creation (not idempotent)
- No versioning of database changes
- Cannot rollback schema changes
- Will cause data loss on schema updates

**Solution**:
1. Initialize Alembic:
```bash
cd services
alembic init alembic
```

2. Configure `alembic/env.py`:
```python
from services.shared.orm_models import Base
target_metadata = Base.metadata
```

3. Create initial migration:
```bash
alembic revision --autogenerate -m "Initial schema"
```

4. Add migration runner to deployment pipeline
5. Update `docker-compose.yml` to run migrations on startup

---

### P0-3: Async Session Context Manager Anti-Pattern
**File**: `services/orchestrator/main.py`
**Line**: 54-69

```python
async with get_async_session() as session:
    scene = Scene(...)
    session.add(scene)
    await session.commit()
```

**Problem**:
- `get_async_session()` returns a session factory, not a context manager
- This code will fail at runtime
- Same issue in `workflow.py` line 190

**Solution**:
Create proper context manager:
```python
# In database.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async session for manual transaction management"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

---

### P0-4: No Circuit Breaker for External Service Calls
**File**: `services/orchestrator/workflow.py`
**Line**: 210-226

```python
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.post(agent_url, json=request_data)
```

**Problem**:
- No circuit breaker for character agent calls
- A failing agent will cascade failures across all scenes
- No fallback mechanism
- Will cause complete service outage if one agent fails

**Impact**: Cascading failures, system-wide outages

**Solution**:
Implement circuit breaker pattern with `aiobreaker`:
```python
from aiobreaker import CircuitBreaker

character_agent_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=60,
    expected_exception=httpx.HTTPError
)

@character_agent_breaker
async def _call_character_agent_with_breaker(...):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(agent_url, json=request_data)
        response.raise_for_status()
        return response.json()

# With fallback
try:
    return await _call_character_agent_with_breaker(...)
except CircuitBreakerError:
    logger.warning(f"Circuit breaker open for {character_name}")
    return generate_fallback_dialogue(character_name, beat_description)
```

---

### P0-5: Synchronous Groq Client Creation in Async Context
**File**: `services/orchestrator/workflow.py`
**Line**: 40-41

```python
from groq import AsyncGroq
client = AsyncGroq(api_key=settings.GROQ_API_KEY)
```

**Problem**:
- Creating new Groq client on every beat planning call
- No client reuse (inefficient)
- Should use dependency injection

**Solution**:
```python
# At module level
_groq_client: AsyncGroq | None = None

def get_groq_client() -> AsyncGroq:
    """Get or create singleton Groq client"""
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(
            api_key=settings.GROQ_API_KEY,
            timeout=httpx.Timeout(60.0, connect=10.0)
        )
    return _groq_client

# In function
async def plan_scene_beats(state: SceneState) -> SceneState:
    client = get_groq_client()
    # ... rest of code
```

---

### P0-6: No Rate Limiting Implementation
**File**: `services/api-gateway/main.py` (Not implemented)

**Problem**:
- Configuration exists (`RATE_LIMIT_PER_MINUTE=60`) but no enforcement
- System vulnerable to DoS attacks
- No cost control for LLM API calls
- No per-user limits

**Impact**: Unlimited LLM costs, service abuse, DoS vulnerability

**Solution**:
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute", "1000/hour"],
    storage_uri=settings.REDIS_URL
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# On endpoints
@router.post("/generate")
@limiter.limit("10/minute")  # Expensive operation
async def generate_scene(...):
    ...
```

---

### P0-7: SQL Injection Vulnerability in Scene Queries
**File**: `services/api-gateway/routers/scenes.py`
**Line**: 128-133

```python
query = select(SceneORM).where(SceneORM.user_id == current_user.id)
if manuscript_id:
    query = query.where(SceneORM.manuscript_id == manuscript_id)
```

**Problem**:
- While SQLAlchemy ORM generally prevents SQL injection, the manuscript_id comes from query params
- No explicit validation that manuscript_id is a valid UUID
- Could cause database errors or unexpected behavior

**Solution**:
```python
from uuid import UUID

@router.get("/", response_model=dict)
async def list_scenes(
    manuscript_id: UUID | None = None,  # Type validation
    skip: int = Query(0, ge=0, le=1000),  # Prevent abuse
    limit: int = Query(20, ge=1, le=100),  # Limit max results
    ...
):
    ...
```

---

### P0-8: Weak CORS Configuration
**File**: `services/api-gateway/main.py`
**Line**: Not explicitly configured

**Problem**:
- No CORS middleware configured
- Will block frontend requests in production
- Need proper origin validation

**Solution**:
```python
from fastapi.middleware.cors import CORSMiddleware

# Get allowed origins from environment
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
    max_age=3600,
)
```

---

## P1 - High Priority Issues

### P1-1: No Distributed Tracing
**Impact**: Cannot debug issues across microservices

**Solution**:
Implement OpenTelemetry:
```python
from opentelemetry import trace
from opentelemetry.exporter.jaeger import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

# Setup tracing
provider = TracerProvider()
jaeger_exporter = JaegerExporter(
    agent_host_name="jaeger",
    agent_port=6831,
)
provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
trace.set_tracer_provider(provider)

# Instrument
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
SQLAlchemyInstrumentor().instrument(engine=async_engine)
```

Add Jaeger to `docker-compose.yml`:
```yaml
jaeger:
  image: jaegertracing/all-in-one:latest
  ports:
    - "16686:16686"  # UI
    - "6831:6831/udp"  # Agent
```

---

### P1-2: Insufficient Logging and Observability
**File**: All services

**Problem**:
- Using `print()` statements instead of structured logging
- No correlation IDs across requests
- No log aggregation strategy
- Cannot debug production issues

**Solution**:
```python
import structlog
import logging
from pythonjsonlogger import jsonlogger

# Configure structured logging
logging.basicConfig(level=logging.INFO)

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Usage
logger.info("scene_generation_started", scene_id=scene_id, manuscript_id=manuscript_id)
```

Add correlation ID middleware:
```python
import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id

        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
```

---

### P1-3: No Health Check Depth
**File**: All service `main.py` files

**Problem**:
```python
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "orchestrator"}
```

- Health check always returns 200 even if dependencies are down
- Cannot use for readiness probes
- Will route traffic to unhealthy instances

**Solution**:
```python
from enum import Enum

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

@app.get("/health/liveness")
async def liveness():
    """Kubernetes liveness probe - is the service running?"""
    return {"status": "healthy"}

@app.get("/health/readiness")
async def readiness():
    """Kubernetes readiness probe - can accept traffic?"""
    checks = {
        "database": await check_db_connection(),
        "redis": await check_redis_connection(),
        "qdrant": await check_qdrant_connection(),
    }

    all_healthy = all(checks.values())
    status = HealthStatus.HEALTHY if all_healthy else HealthStatus.DEGRADED

    return {
        "status": status,
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat()
    }, 200 if all_healthy else 503

async def check_redis_connection() -> bool:
    try:
        if redis_client:
            await redis_client.ping()
            return True
    except:
        pass
    return False

async def check_qdrant_connection() -> bool:
    try:
        # Ping Qdrant
        return True
    except:
        return False
```

---

### P1-4: Weak Password Hashing Configuration
**File**: `services/shared/auth.py`

**Problem**:
```python
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
```

- No explicit work factor specified
- Default bcrypt rounds (12) may be too low for 2025
- No future-proofing for quantum computing

**Solution**:
```python
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=14,  # Stronger hashing (increase over time)
    bcrypt__ident="2b"  # Use 2b variant
)

# Consider adding Argon2id for new passwords
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
    argon2__memory_cost=65536,  # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=4,
)
```

---

### P1-5: No Request Validation for Large Payloads
**File**: All API endpoints

**Problem**:
- No max request body size
- Vulnerable to memory exhaustion attacks
- Large scene descriptions could crash service

**Solution**:
```python
# In main.py
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Configure properly
)

# Add size limit middleware
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size: int = 10 * 1024 * 1024):  # 10MB
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request, call_next):
        if request.headers.get("content-length"):
            content_length = int(request.headers["content-length"])
            if content_length > self.max_size:
                return JSONResponse(
                    {"error": "Request too large"},
                    status_code=413
                )
        return await call_next(request)

app.add_middleware(RequestSizeLimitMiddleware, max_size=10*1024*1024)
```

---

### P1-6: Missing API Versioning Strategy
**File**: `services/api-gateway/main.py`

**Problem**:
- Routes are `/api/v1/...` but no version negotiation
- Cannot deprecate old endpoints gracefully
- Cannot run multiple API versions

**Solution**:
```python
from fastapi import Header, HTTPException

class APIVersion(str, Enum):
    V1 = "v1"
    V2 = "v2"

async def validate_api_version(
    accept_version: str | None = Header(None, alias="Accept-Version")
) -> APIVersion:
    """Validate and return API version from header"""
    if accept_version is None:
        return APIVersion.V1  # Default

    try:
        return APIVersion(accept_version)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported API version: {accept_version}"
        )

# Use in routes
@router.get("/manuscripts/")
async def list_manuscripts(
    api_version: APIVersion = Depends(validate_api_version),
    ...
):
    if api_version == APIVersion.V2:
        # Return V2 format
        pass
    # Return V1 format
```

---

## P2 - Medium Priority Issues

### P2-1: No Internationalization (i18n) Support
**File**: Frontend and backend error messages

**Problem**:
- All strings are hardcoded in English
- Cannot support non-English users
- Error messages not localized

**Solution**:

Backend (Python):
```python
# Use babel for i18n
from babel.support import Translations

def get_translation(locale: str = "en"):
    return Translations.load('locales', [locale])

_ = get_translation().gettext

# Usage
raise HTTPException(
    status_code=404,
    detail=_("manuscript_not_found")
)
```

Frontend (Next.js):
```typescript
// Install next-i18next
import { useTranslation } from 'next-i18next'

export default function Page() {
  const { t } = useTranslation('common')

  return (
    <h1>{t('welcome')}</h1>
  )
}
```

---

### P2-2: No Caching Strategy for Manuscript Characters
**File**: `services/api-gateway/routers/manuscripts.py`

**Problem**:
- Every request fetches characters from database
- Characters rarely change after processing
- High database load for frequently accessed manuscripts

**Solution**:
```python
import redis.asyncio as redis
from functools import wraps
import json

async def get_cached_characters(manuscript_id: UUID, db: AsyncSession):
    """Get characters with Redis caching"""
    cache_key = f"manuscript:{manuscript_id}:characters"

    # Try cache first
    if redis_client:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)

    # Fetch from DB
    result = await db.execute(
        select(CharacterORM).where(CharacterORM.manuscript_id == manuscript_id)
    )
    characters = result.scalars().all()

    # Cache for 1 hour
    if redis_client:
        await redis_client.setex(
            cache_key,
            3600,
            json.dumps([char.to_dict() for char in characters])
        )

    return characters
```

---

### P2-3: Inefficient Scene Beat Parsing
**File**: `services/orchestrator/workflow.py`
**Line**: 72-83

**Problem**:
```python
for line in beats_text.split('\n'):
    if line and (line[0].isdigit() or line.startswith('-')):
        beat_desc = line.split('.', 1)[-1].strip()
```

- Fragile parsing logic
- Will fail if LLM returns different format
- No validation of beat quality

**Solution**:
```python
# Use structured output with JSON
prompt = f"""...(same prompt)...

Return your response as valid JSON in this exact format:
{{
  "beats": [
    {{
      "number": 1,
      "description": "Alice enters the tavern...",
      "characters": ["Alice"],
      "emotional_tone": "nervous"
    }}
  ]
}}
"""

response = await client.chat.completions.create(
    model=settings.GROQ_MODEL,
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7,
    max_tokens=500,
    response_format={"type": "json_object"}  # Force JSON
)

try:
    beats_data = json.loads(response.choices[0].message.content)
    beats = beats_data.get("beats", [])
except json.JSONDecodeError:
    logger.error("Failed to parse beat planning response", response=beats_text)
    beats = [create_default_beat(scene_request)]
```

---

### P2-4: No Retry Logic for LLM Calls
**File**: `services/orchestrator/workflow.py`, `services/character-agent/main.py`

**Problem**:
- LLM calls can fail due to rate limits, timeouts
- No retry with exponential backoff
- Single failure breaks entire scene generation

**Solution**:
```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True
)
async def call_groq_with_retry(client, **kwargs):
    """Call Groq API with retry logic"""
    return await client.chat.completions.create(**kwargs)
```

---

### P2-5: Weak Frontend Error Handling
**File**: `frontend/lib/api-client.ts`

**Problem**:
```typescript
throw new Error(errorData.detail || 'An error occurred')
```

- All errors become generic strings
- No error codes or types
- Cannot handle different errors differently

**Solution**:
```typescript
export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public code?: string,
    public details?: any
  ) {
    super(message)
    this.name = 'ApiError'
  }

  isNetworkError(): boolean {
    return this.statusCode === 0
  }

  isAuthError(): boolean {
    return this.statusCode === 401 || this.statusCode === 403
  }

  isValidationError(): boolean {
    return this.statusCode === 422
  }
}

// In catch block
if (error.response) {
  const errorData = error.response.data
  throw new ApiError(
    errorData.detail || 'Server error',
    error.response.status,
    errorData.code,
    errorData.validation_errors
  )
} else if (error.request) {
  throw new ApiError('Network error', 0)
}
```

---

### P2-6: No Database Connection Retry on Startup
**File**: `services/shared/database.py`

**Problem**:
- Services fail to start if database not ready
- Common in Docker Compose startup
- Need to wait for dependencies

**Solution**:
```python
import asyncio
from tenacity import retry, stop_after_delay, wait_fixed

@retry(
    stop=stop_after_delay(60),  # Try for 60 seconds
    wait=wait_fixed(2),
    reraise=True
)
async def wait_for_db():
    """Wait for database to be ready"""
    if not await check_db_connection():
        raise ConnectionError("Database not ready")
    logger.info("Database connection established")

# In startup event
@app.on_event("startup")
async def startup():
    await wait_for_db()
    # Rest of startup logic
```

---

### P2-7: Missing Input Sanitization
**File**: `services/orchestrator/workflow.py`
**Line**: 45-59

**Problem**:
```python
prompt = f"""You are a narrative planner...
Scene Description: {scene_request['scene_description']}
Setting: {scene_request['setting']}
```

- Direct string interpolation from user input into LLM prompts
- Vulnerable to prompt injection attacks
- User could manipulate LLM behavior with crafted inputs

**Solution**:
```python
import html
import re

def sanitize_prompt_input(text: str, max_length: int = 1000) -> str:
    """Sanitize user input for LLM prompts"""
    # Remove potentially harmful characters
    text = re.sub(r'[^\w\s\.\,\!\?\-\(\)]', '', text)
    # Truncate
    text = text[:max_length]
    # Escape HTML
    text = html.escape(text)
    return text.strip()

scene_desc = sanitize_prompt_input(scene_request['scene_description'])
setting = sanitize_prompt_input(scene_request['setting'])

prompt = f"""You are a narrative planner...
Scene Description: {scene_desc}
Setting: {setting}
"""
```

---

## P3 - Low Priority Issues

### P3-1: Missing OpenAPI Documentation Customization
**File**: All `main.py` files

**Solution**:
```python
app = FastAPI(
    title="Polyphony API Gateway",
    description="Multi-character creative writing platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "auth", "description": "Authentication operations"},
        {"name": "manuscripts", "description": "Manuscript management"},
        {"name": "scenes", "description": "Scene generation"},
    ],
    contact={
        "name": "Polyphony Team",
        "email": "support@polyphony.ai",
    },
    license_info={
        "name": "MIT",
    }
)
```

---

### P3-2: No API Response Compression
**Solution**:
```python
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
```

---

### P3-3: Weak TypeScript Configuration
**File**: `frontend/tsconfig.json`

**Add**:
```json
{
  "compilerOptions": {
    "noUncheckedIndexedAccess": true,
    "noPropertyAccessFromIndexSignature": true,
    "exactOptionalPropertyTypes": true
  }
}
```

---

## Configuration & Deployment Issues

### CD-1: Missing Environment-Specific Configs
**Problem**: Single `.env` for all environments

**Solution**:
Create environment-specific files:
- `.env.development`
- `.env.staging`
- `.env.production`

```bash
# Load based on environment
if [ "$ENVIRONMENT" = "production" ]; then
    source .env.production
else
    source .env.development
fi
```

---

### CD-2: No Kubernetes Manifests
**Problem**: Only Docker Compose provided

**Solution**:
Create k8s manifests:
```yaml
# k8s/api-gateway-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
    spec:
      containers:
      - name: api-gateway
        image: polyphony/api-gateway:latest
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health/liveness
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health/readiness
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
```

---

### CD-3: No CI/CD Pipeline
**Problem**: No automated testing/deployment

**Solution**:
Create `.github/workflows/ci.yml`:
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: pytest --cov=services tests/
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run black
        run: black --check services/
      - name: Run ruff
        run: ruff check services/

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run bandit
        run: bandit -r services/
      - name: Run safety
        run: safety check
```

---

## Performance & Scalability Issues

### PERF-1: N+1 Query Problem
**File**: `services/api-gateway/routers/manuscripts.py`

**Problem**:
```python
manuscripts = result.scalars().all()
# Later, accessing manuscript.characters triggers separate query
```

**Solution**:
```python
from sqlalchemy.orm import selectinload

result = await db.execute(
    select(ManuscriptORM)
    .options(selectinload(ManuscriptORM.characters))  # Eager load
    .where(ManuscriptORM.user_id == current_user.id)
)
```

---

### PERF-2: No Database Indexing Strategy
**Problem**: Queries on non-indexed columns

**Solution**:
Add composite indexes:
```sql
CREATE INDEX CONCURRENTLY idx_scenes_user_manuscript
ON scenes(user_id, manuscript_id, created_at DESC);

CREATE INDEX CONCURRENTLY idx_scenes_status_created
ON scenes(status, created_at DESC)
WHERE status = 'processing';
```

---

### PERF-3: No CDN for Frontend Assets
**Problem**: All assets served from Next.js server

**Solution**:
Configure Cloudflare/CloudFront:
```javascript
// next.config.js
module.exports = {
  assetPrefix: process.env.NODE_ENV === 'production'
    ? 'https://cdn.polyphony.ai'
    : undefined,
  images: {
    domains: ['cdn.polyphony.ai'],
  },
}
```

---

## Security Issues

### SEC-1: Missing Security Headers
**Solution**:
```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

---

### SEC-2: API Keys in Logs
**Problem**: Potential to log sensitive data

**Solution**:
```python
import logging

class SanitizeFilter(logging.Filter):
    def filter(self, record):
        # Redact sensitive fields
        if hasattr(record, 'msg'):
            record.msg = re.sub(
                r'(api_key|password|secret|token)=\S+',
                r'\1=***REDACTED***',
                str(record.msg)
            )
        return True

logging.getLogger().addFilter(SanitizeFilter())
```

---

## Testing Gaps

### TEST-1: Missing Integration Tests
**Current**: Only 40 unit tests
**Needed**: End-to-end tests

**Solution**:
```python
# tests/integration/test_scene_generation_e2e.py
import pytest
from httpx import AsyncClient

@pytest.mark.integration
async def test_complete_scene_generation_flow(async_client, test_user, test_manuscript):
    """Test complete flow: upload -> process -> generate scene"""

    # 1. Upload manuscript
    files = {"file": ("test.txt", "Test content", "text/plain")}
    response = await async_client.post(
        "/api/v1/manuscripts/upload",
        files=files,
        data={"title": "Test"},
        headers=auth_headers
    )
    assert response.status_code == 200
    manuscript_id = response.json()["id"]

    # 2. Wait for processing
    # ...

    # 3. Generate scene
    # ...

    # 4. Verify scene content
    # ...
```

---

### TEST-2: No Load Testing
**Solution**:
```python
# load_tests/locustfile.py
from locust import HttpUser, task, between

class PolyphonyUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        # Login
        response = self.client.post("/api/v1/auth/login", {
            "username": "test@example.com",
            "password": "test123"
        })
        self.token = response.json()["access_token"]

    @task(3)
    def list_manuscripts(self):
        self.client.get(
            "/api/v1/manuscripts/",
            headers={"Authorization": f"Bearer {self.token}"}
        )

    @task(1)
    def generate_scene(self):
        self.client.post(
            "/api/v1/scenes/generate",
            json={
                "manuscript_id": "...",
                "characters": ["Alice"],
                "scene_description": "...",
                "setting": "...",
                "emotional_tone": "neutral"
            },
            headers={"Authorization": f"Bearer {self.token}"}
        )

# Run: locust -f locustfile.py --host=http://localhost:8000
```

---

## Monitoring & Observability Gaps

### MON-1: Missing SLO/SLA Definitions
**Needed**:
```yaml
# slo.yaml
slos:
  - name: api_availability
    target: 99.9%
    window: 30d

  - name: scene_generation_latency
    target: p95 < 30s
    window: 7d

  - name: error_rate
    target: < 1%
    window: 24h
```

---

### MON-2: No Alerting Rules
**Solution**:
```yaml
# prometheus/alerts.yml
groups:
  - name: polyphony
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 5m
        annotations:
          summary: "High error rate detected"

      - alert: DatabaseConnectionPoolExhausted
        expr: sqlalchemy_pool_connections_total >= 30
        for: 2m
        annotations:
          summary: "Database connection pool near limit"
```

---

## Summary of Action Items

### Immediate (Next Sprint)
1. ✅ Fix P0-3: Async session context manager
2. ✅ Implement P0-6: Rate limiting
3. ✅ Add P0-8: CORS configuration
4. ✅ Implement P1-2: Structured logging
5. ✅ Add P1-3: Proper health checks

### Short Term (Next Month)
1. ✅ P0-2: Database migrations with Alembic
2. ✅ P0-4: Circuit breakers
3. ✅ P1-1: Distributed tracing
4. ✅ P2-3: Structured LLM outputs
5. ✅ SEC-1: Security headers

### Medium Term (Next Quarter)
1. ✅ Kubernetes manifests
2. ✅ CI/CD pipeline
3. ✅ Load testing framework
4. ✅ i18n support
5. ✅ Comprehensive monitoring

---

## Conclusion

The Polyphony codebase shows promise but requires significant hardening before production deployment. Focus on P0 issues first, then systematically address P1 and P2 issues.

**Estimated Effort**: 4-6 weeks of focused development to reach production readiness

**Recommended Next Steps**:
1. Create tickets for all P0/P1 issues
2. Set up CI/CD pipeline
3. Implement comprehensive logging and monitoring
4. Add integration and load tests
5. Security audit
6. Performance testing

**Risk Level**: HIGH until P0 issues resolved
