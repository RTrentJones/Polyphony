# Polyphony

A multi-user, multi-character AI book-writing platform. Upload a manuscript
(or build a character bible by hand), index each character's voice into a
vector store, then generate scene drafts where every character speaks in their
own retrieval-grounded voice — and revise, structure, and export the result as
a book.

Deployed as a [Greenlight](https://github.com/RTrentJones/greenlight) tool of
[rtrentjones.dev](https://github.com/RTrentJones/RTrentJones.dev) at
`polyphony.rtrentjones.dev` (single container on OCI behind a Cloudflare
tunnel; Neon Postgres with pgvector; Gemini free tier by default).

> **What the product is meant to do lives in [docs/BRD.md](docs/BRD.md)** — the source
> of truth for Canon (a book's authored truth) and generation behaviour. Change it
> first, then the code. The catalogue is [docs/feature-set.md](docs/feature-set.md);
> the decisions are in [docs/ADR-002-book-as-root.md](docs/ADR-002-book-as-root.md).
>
> The original microservices design spec lives at
> [docs/archive/DESIGN-SPEC-v1.md](docs/archive/DESIGN-SPEC-v1.md); the
> consolidation decisions are recorded in
> [docs/ADR-001-consolidation.md](docs/ADR-001-consolidation.md).

## Architecture

One FastAPI application (`app/`) plus a statically-exported Next.js frontend
served same-origin:

```
app/
  main.py          # the FastAPI app: middleware, routers, /health, /__version
  core/            # config, database (async SQLAlchemy), ORM models, security
                   #   (JWT + refresh rotation), resilience, sanitization,
                   #   metrics, logging, caching (in-process), budget
  llm/             # provider-fungible LLM backend (see below)
  rag/             # fastembed (ONNX) embeddings + pgvector store (same DB)
  characters/      # character-voice dialogue generation (characters = data)
  orchestration/   # scene workflow: plan beats -> dialogue -> assemble
  parsing/         # document parsing + character extraction pipeline
  api/             # routers: auth, manuscripts, scenes, characters
alembic/           # migrations (run automatically at container start)
frontend/          # Next.js 15 App Router -> static export
```

Stores: ONE Postgres (SQLAlchemy async; `DATABASE_URL` or `POSTGRES_*`) that
also holds the vector search — a pgvector `voice_chunks` table (HNSW, cosine)
filtered per character/user. In-process caches (no Redis).

## The LLM backend is fungible

`app/llm/providers.py` is a registry of OpenAI-compatible providers (Gemini,
Groq, xAI, OpenAI) — one `openai` SDK client parameterized by `base_url`.
Select with env config, no code changes:

```
LLM_PROVIDER=gemini          # default; needs GEMINI_API_KEY
LLM_MODEL=                   # optional override (default gemini-2.5-flash)
LLM_MODEL_FAST=              # optional override (default gemini-2.5-flash-lite)
```

Every call goes through `app/llm/client.py`: per-provider RPM pacing (free-tier
friendly), retry + circuit breaker, Prometheus token/cost metrics, and
per-user usage accounting (`api_usage` table) enforced by a rolling daily
budget (`USER_DAILY_TOKEN_LIMIT`).

## Auth

Multi-user JWT auth, hardened:

- **Invite-gated registration** — `POST /api/v1/auth/register` requires a
  valid invite code (admins mint them via `POST /api/v1/auth/invites` or
  `python -m app.cli create-invite`).
- Access tokens (30 min, in memory) + **rotating refresh tokens** in an
  httpOnly `SameSite=Strict` cookie; reuse detection revokes the family.
- `is_active` account states, admin role, rate-limited login/register.
- First boot: `ADMIN_EMAIL`/`ADMIN_PASSWORD` create the admin user when the
  users table is empty.

## Develop

```bash
cp .env.example .env         # set SECRET_KEY, GEMINI_API_KEY, POSTGRES_PASSWORD
make install                 # pip install -r requirements.txt
make test                    # pytest (the CI ship gate)
make lint                    # black --check + ruff

make dev                     # compose profile dev: postgres (pgvector) + app (reload)
make preview                 # compose profile preview: the as-shipped image
```

The frontend in dev: `cd frontend && npm install && npm run dev` (talks to
`localhost:8000` via the Next.js rewrite).

## Ship

Pushing to `main` runs the test-gated container build
(`.github/workflows/greenlight-build.yml`): pytest → build `linux/arm64` image
→ push to GHCR → `repository_dispatch` to RTrentJones.dev, whose
`greenlight-deploy-polyphony.yml` restarts the OCI instance and runs the
SHA-gated verify (`/__version` must match the shipped commit). See the wrapper
repo's `verify/polyphony.config.ts` for the prod gate.
