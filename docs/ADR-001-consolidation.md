# ADR-001: Consolidation to a single container, provider-fungible LLM backend

**Status**: Accepted
**Date**: 2026-07-10
**Context**: Polyphony is being adopted into RTrentJones.dev as a Greenlight tool
(`lane: mcp`, `target: oci`), deployed as ONE container on an OCI Always-Free
Ampere A1 instance (1 OCPU / 6 GB) behind a Cloudflare tunnel, with managed
free-tier data stores. The original microservice topology (api-gateway,
orchestrator, document-parser, one container **per character**, Postgres,
Qdrant, Redis, Prometheus, Grafana — 12 compose services) cannot fit that
target, and several of its seams were the direct cause of the defects that
broke the product end to end (scenes double-inserted across the
gateway/orchestrator seam, scenes missing `user_id`, an internal
service-discovery string hack, unauthenticated internal services).

## Decisions

### 1. One FastAPI application (`app/`), not microservices
All four Python services merge into a single package `app/` with clean module
boundaries (`core`, `llm`, `rag`, `characters`, `orchestration`, `parsing`,
`api`). Inter-service HTTP hops become function calls. Characters become
**data** (DB rows + vector-store payloads), not containers — the
per-character deployments (hermione/harry/ron) are deleted.

Consequences: the gateway↔orchestrator seam that produced the double
Scene insert and the missing `user_id` disappears structurally; the
hyphenated-directory `sys.path` hacks die; one Dockerfile ships the whole
backend plus the statically-exported frontend.

### 2. LLM provider fungibility via an OpenAI-compatible registry (`app/llm/`)
Direct Groq SDK calls (3 call sites, one with a hardcoded decommissioned
model) are replaced by a provider registry modeled on
`RTrentJones.dev/tools/tracer/lib/providers.ts`: one `openai` SDK client
parameterized by `base_url` covers Gemini / Groq / xAI / OpenAI; each provider
is gated on its own env key. `LLM_PROVIDER=gemini` is the default, fed by the
account-shared `GEMINI_API_KEY` GitHub secret. Per-provider rate pacing,
retry + circuit breaker, and token/cost accounting live in the client, not at
call sites.

### 3. Embeddings: fastembed (ONNX), not sentence-transformers/torch
`sentence-transformers` drags ~2 GB of torch into an arm64 image and ~1 GB of
RSS — untenable on a 6 GB instance sharing the free cap with bamcp. fastembed
runs the same `all-MiniLM-L6-v2` model on ONNX Runtime, keeping the exact
384-dim vector geometry (`EMBEDDING_DIMENSION=384` unchanged, existing
collection layouts remain valid). The model is baked into the image at build
time so container restarts don't re-download.

Gemini's embedding API was rejected: it would spend the same 10–15 RPM
free-tier budget generation needs, in the worst place (bulk indexing).

### 4. Redis dropped; in-process state
One container means in-process state is correct: `cachetools.TTLCache`
replaces the Redis cache-aside, slowapi uses `memory://` storage, and the
dialogue cache is in-process. Redis, `hiredis`, and `fakeredis` leave the
dependency tree.

### 5. LangGraph/LangChain dropped
The scene workflow is a linear plan → loop-over-beats → assemble pipeline;
as plain `async` functions it loses nothing and sheds `langchain`,
`langgraph`, and `langchain-groq` from the image.

### 6. Shared Qdrant collection with payload-filter multitenancy
One collection (`polyphony_chunks`) with payload indexes on
`character_id` / `user_id` / `book_id`, instead of a collection per character.
This is Qdrant's own multitenancy guidance for small clusters and is the only
sane layout on the 1 GB Cloud free tier for a multi-user system. Per-character
retrieval isolation is preserved via filters. `Character.qdrant_collection_name`
remains in the schema (holding the shared name) so reverting is cheap.

### 7. Managed free-tier stores
Neon Postgres (direct URL, small SQLAlchemy pool — the container is
long-lived, so the pooled/PgBouncer URL is unnecessary and asyncpg-hostile)
and Qdrant Cloud (1 GB free). Alembic owns the schema from migration 0001;
`scripts/init-db.sql` is retired.

## Alternatives considered
- **`target: docker` on a self-owned host** (keep the compose topology):
  rejected — no always-on host in play, and the microservice seams were a
  defect source, not a scaling asset, at this size.
- **Keeping LangGraph for the orchestration graph**: contained swap either
  way; removed for image size and dependency surface.

## Amendment (2026-07-10): pgvector replaces Qdrant Cloud (§6, §7)

Decision 6's shared-collection layout survives, but the store is now a
`voice_chunks` table (`vector(384)`, HNSW, cosine) in the SAME Neon Postgres
via the `pgvector` extension — not a Qdrant Cloud cluster. Rationale: Neon is
already provisioned by the wrapper's Terraform and its connection string
already reaches the container; Qdrant Cloud added a hand-provisioned account
and two secrets for no capability we need at this scale (384-dim, ≲500k
chunks). The table lives only in the Alembic baseline (postgres-only guard)
so sqlite test databases never see a vector column; `app/rag/store.py` keeps
the same ChunkStore interface over raw SQL.

If scale ever warrants a dedicated vector store, the path is upstreaming
`data: 'qdrant'` as a first-class Greenlight data source (schema matrix +
provider pack + Terraform module) — never hand-wiring it in this consumer.
