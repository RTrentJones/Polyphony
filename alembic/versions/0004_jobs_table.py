"""Durable background jobs table.

Background work (scene generation, manuscript processing, continuity checks)
moves from in-process FastAPI BackgroundTasks to Postgres-backed jobs claimed
by an in-process worker loop (FOR UPDATE SKIP LOCKED). A job row commits
atomically with its domain row, so work survives restarts.

Also a one-time cleanup: rows stuck in 'processing' from the BackgroundTasks
era can never complete (their tasks died with a previous container), so they
are flipped to 'failed'. Safe at deploy time — the deploy restarts the single
container, killing any genuinely in-flight background task anyway.

Revision ID: 0004
Revises: 0003

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # sqlite tests build the current ORM shape from the 0001 baseline.
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind VARCHAR(50) NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            locked_at TIMESTAMPTZ,
            locked_by VARCHAR(100),
            available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            error TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status_available "
        "ON jobs (status, available_at)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs (kind)")

    op.execute("UPDATE scenes SET status = 'failed' WHERE status = 'processing'")
    op.execute("UPDATE manuscripts SET status = 'failed' WHERE status = 'processing'")
    op.execute(
        "UPDATE continuity_reports SET status = 'failed' WHERE status = 'processing'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # The stuck-row cleanup is irreversible by design.
    op.execute("DROP TABLE IF EXISTS jobs")
