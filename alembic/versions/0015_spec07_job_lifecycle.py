"""jobs lifecycle — pause bound, cancel, checkpoint, dedupe, domain link (SPEC-07/08).

Revision ID: spec07_job_lifecycle
Revises: spec11_drop_character_chunks
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "spec07_job_lifecycle"
down_revision: Union[str, None] = "spec11_drop_character_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS paused_since TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pause_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN "
        "NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS checkpoint JSONB")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(255)")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS domain_type VARCHAR(30)")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS domain_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_domain ON jobs (domain_type, domain_id)"
    )
    # One in-flight job per dedupe key; a paused job is status 'queued', so a
    # pause keeps its key too.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_dedupe_active ON jobs (dedupe_key) "
        "WHERE status IN ('queued','running')"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS uq_jobs_dedupe_active")
    op.execute("DROP INDEX IF EXISTS idx_jobs_domain")
    for col in (
        "domain_id",
        "domain_type",
        "dedupe_key",
        "checkpoint",
        "cancel_requested",
        "pause_count",
        "paused_since",
    ):
        op.execute(f"ALTER TABLE jobs DROP COLUMN IF EXISTS {col}")
