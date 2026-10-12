# Working on specs in parallel

Several specs are implemented at once, each by its own agent (or person) on its own branch,
and a coordinator integrates them onto one branch. This file is the contract that keeps that
safe. It applies to anyone implementing a spec, not only to agents.

## The loop every spec follows

1. Read the spec, the [BRD](../BRD.md) section it touches, and the `polyphony-canon` skill.
2. Implement the acceptance criteria. Each criterion gets a test (or, where a spec says so, an
   eval gate or a recorded manual check).
3. Run the gates below until all are green.
4. Update the BRD / feature-set rows the spec lists, in the same branch.
5. Commit on your branch with a message that names the spec. Do not push; the coordinator
   integrates.
6. Report: what shipped, what each acceptance criterion maps to, and any gap you could not
   close (with the reason), so the coordinator can carry it.

## Gates (all must pass before you report done)

```bash
PY=/tmp/claude-0/venv/bin/python   # Python 3.12 venv with requirements.txt installed
export SECRET_KEY=ci_secret_key_minimum_32_characters_long POSTGRES_PASSWORD=ci_password_12345

# unit + sqlite suite (~4 min)
$PY -m pytest tests -q --no-cov -p no:cacheprovider

# lint (CI-pinned versions are installed in the venv)
$PY -m black --check app tests alembic evals && $PY -m ruff check app tests alembic evals

# Postgres integration suite, against YOUR OWN database (see "Isolation")
docker exec pg psql -U postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
RUN_PG_TESTS=1 DATABASE_URL=postgresql+asyncpg://postgres:ci_password_12345@localhost:5432/$DB \
  $PY -m pytest tests/integration -m integration -o addopts="" -q -p no:cacheprovider

# end-to-end: the real app + the stub LLM through the HTTP API, with eval gates
docker exec pg psql -U postgres -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
APP_PORT=$APP_PORT STUB_PORT=$STUB_PORT PYTHON=$PY \
  DATABASE_URL=postgresql+asyncpg://postgres:ci_password_12345@localhost:5432/$DB \
  scripts/e2e_stub.sh

# frontend (if you touched frontend/)
cd frontend && npm run type-check && npm run lint && npm run build
```

`evals/stub_llm.py` answers only from what each prompt contains, so the e2e gate catches a
pipeline that stops delivering canon to the model. Extend its prompt families when you add a
new generator prompt (keep each answer derived from the prompt, never canned truth).

## Isolation

- **Database**: one shared pgvector container named `pg` on port 5432. Use your own database
  name (`polyphony_<spec>`), never another's.
- **Ports**: use the `APP_PORT`/`STUB_PORT` pair you were assigned. Never kill processes you
  did not start; never `pkill -f` a pattern that can match another agent's process (or your own
  shell — a pattern inside the command line matches the shell running it).
- **Frontend `node_modules`**: install in your own worktree (`npm ci`) if you need it.

## Migrations

Revision ids must not collide across branches. Name your migration
`alembic/versions/0012_<spec>_<slug>.py` with `revision = "<spec>_<slug>"` (for example
`spec04_scene_book_id`) and `down_revision = "0011"`. The coordinator re-chains them linearly
when integrating. Every migration is postgres-only (sqlite tests build the ORM shape), uses
`IF [NOT] EXISTS` guards, and has a working `downgrade`.

## Shared files

Keep edits to these files small and local, because every branch touches them:
`app/core/orm_models.py`, `app/jobs/handlers.py`, `app/api/books.py`, `app/api/plans.py`,
`frontend/lib/types.ts`, `frontend/app/books/detail/page.tsx`. Prefer adding a new module or
component and wiring it in with a few lines over rewriting a shared function. Never reformat a
file you did not otherwise change.

## Conventions that are not optional

- Prompt assembly goes through `Canon`; untrusted content is fenced with `as_quoted_block` under
  `STORY_MATERIAL_NOTICE`; no string slice of author content in a prompt (ADR-002 §4).
- Every LLM-spending endpoint enqueues a job, preflights its estimate, and every job kind has
  `on_dead`, `on_pause` and `on_cancel` hooks (SPEC-07).
- Nothing the author wrote is destroyed: mutations append an `entity_versions` row first.
- Match the surrounding code's comment density and naming; explain *why*, briefly.
