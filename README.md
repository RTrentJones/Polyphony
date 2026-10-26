# Polyphony

[![evals](https://tracer.rtrentjones.dev/api/badge/polyphony?label=evals)](https://tracer.rtrentjones.dev/projects/polyphony)

A multi-user, multi-character AI book-writing platform. The **book is the root
of every concept** (docs/ADR-002-book-as-root.md): you create a book, add
**sources** (uploaded files or pasted text) and characters to it, index each
character's voice into a vector store, then generate scene drafts where every
character speaks in their own retrieval-grounded voice — and revise, structure,
and export the result as a book.

Deployed as a [Greenlight](https://github.com/RTrentJones/greenlight) tool of
[rtrentjones.dev](https://github.com/RTrentJones/RTrentJones.dev) at
`polyphony.rtrentjones.dev` (single container on OCI behind a Cloudflare
tunnel; Neon Postgres with pgvector; Gemini free tier by default).

> **What the product is meant to do lives in [docs/BRD.md](docs/BRD.md)** — the source
> of truth for Canon (a book's authored truth) and generation behaviour. Change it
> first, then the code. The catalogue is [docs/feature-set.md](docs/feature-set.md);
> the decisions are in [docs/ADR-002-book-as-root.md](docs/ADR-002-book-as-root.md).
>
> The current state against the BRD is reviewed in [docs/REVIEW-2026-10.md](docs/REVIEW-2026-10.md),
> and the work it implies is broken into sequenced deliverables in [docs/specs/](docs/specs/README.md).
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
  api/             # routers: auth, books, sources, scenes, characters
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
LLM_PROVIDER=gemini          # default; needs GEMINI_API_KEY (stub = local tests)
LLM_TIER=free                # the one graduation switch: free | paid
LLM_MODEL=                   # optional override (default gemini-3.5-flash-lite)
LLM_MODEL_FAST=              # optional override (default gemini-3.5-flash-lite)
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

**Rate limits** (`app/core/rate_limit.py`) key on the real client, never the
Cloudflare tunnel. The client IP is `CF-Connecting-IP` only when the TCP peer is
in `TRUSTED_PROXY_CIDRS` (default loopback + Docker's private bridge ranges;
from any other peer the header is ignored, and uvicorn runs with
`--no-proxy-headers` so the app alone makes that call). Login is 5/minute per IP
plus `LOGIN_EMAIL_RATE_LIMIT` (20/hour) failed attempts per email across all
IPs, which starts a short lockout (1 minute, doubling per repeated breach, capped
at 15 minutes; never from an address the account signed in from — SPEC-05); register is 3/hour per IP. Everything else is limited per user (the bearer
token's subject, decoded without a DB hit) or per IP when anonymous: the
`RATE_LIMIT_PER_MINUTE`/`_PER_HOUR` defaults per route, a separate
`RATE_LIMIT_POLL` (120/minute) bucket for the GETs the UI polls, and
`RATE_LIMIT_LLM` (10/minute) shared by every LLM-enqueueing POST (the token
budget is the real control). 429s carry `Retry-After`; the UI's polling waits
it out. Each request log line records `peer` and `cf_connecting_ip` so the
tunnel topology can be checked against the trusted CIDRs.

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
`localhost:8000` via the Next.js rewrite). Frontend checks: `npm run type-check`,
`npm run lint`, `npm test` (Vitest), `npm run build`.

### End to end, with no API key

`evals/stub_llm.py` is a deterministic, OpenAI-compatible stub model that answers
only from what each prompt contains, so the whole app can run end to end with no
key, no quota and no cost — and a pipeline that stops delivering the canon to the
model fails visibly. `LLM_PROVIDER=stub` selects it.

```bash
# needs an empty pgvector database at $DATABASE_URL
scripts/e2e_stub.sh                         # API flows + eval gates (cel fidelity, dracula)
(cd frontend && npm ci && npm run build)
RUN_BROWSER_E2E=1 scripts/e2e_stub.sh       # …plus the Playwright browser journey
```

CI runs the same script on every PR (`test.yml` → `e2e`). The live-model evals
(`evals.yml`) are dispatched on demand and gate on `cast_fidelity >= 0.9` for the
`cel` corpus. Those scores are on **development corpora** (renamed public-domain
novels and the synthetic `cel`, tuned on for many waves; renaming reduces
name-cued recall but does not defeat memorization of a famous novel); the frozen
holdout corpora run once per release ([evals/README.md](evals/README.md)). Specs for in-flight work live in [docs/specs](docs/specs/README.md);
parallel work follows [docs/specs/PARALLEL-WORK.md](docs/specs/PARALLEL-WORK.md).

## Ship

Pushing to `main` runs the test-gated container build
(`.github/workflows/greenlight-build.yml`): pytest → build `linux/arm64` image
→ push to GHCR → `repository_dispatch` to RTrentJones.dev, whose
`greenlight-deploy-polyphony.yml` restarts the OCI instance and runs the
SHA-gated verify (`/__version` must match the shipped commit). See the wrapper
repo's `verify/polyphony.config.ts` for the prod gate.
