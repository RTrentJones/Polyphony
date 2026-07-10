# Polyphony — single consolidated image (docs/ADR-001).
# Multi-stage: Node builds the static frontend export; the Python stage runs
# the FastAPI app and serves the export. Built linux/arm64 for OCI Ampere A1.

# --- Stage 1: frontend static export ---------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
# Static export (next.config.js sets output: 'export' → out/)
RUN npm run build

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /srv

# libmagic for upload MIME sniffing; libpq not needed (asyncpg is pure wheel)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the fastembed ONNX model so container restarts don't re-download
ENV FASTEMBED_CACHE_PATH=/srv/.fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY --from=frontend /build/out ./frontend/out

# Artifact identity for Greenlight's SHA-gated verify (/__version)
ARG GREENLIGHT_SHA=""
ENV GREENLIGHT_SHA=${GREENLIGHT_SHA}

ENV ENVIRONMENT=production \
    STATIC_DIR=/srv/frontend/out \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Migrations then serve — single container, no migration races
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
