"""W12: api_usage.model/.job_id (no FK), jobs.pause_seconds; drop the HNSW index.

Revision ID: w12_usage_and_pauses
Revises: refresh_token_family
"""

from alembic import op

revision = "w12_usage_and_pauses"
down_revision = "refresh_token_family"
branch_labels = depends_on = None
HNSW = "ON voice_chunks USING hnsw (embedding vector_cosine_ops)"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS model VARCHAR(100)")
        op.execute("ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS job_id UUID")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_api_usage_job_id ON api_usage (job_id)"
        )
        op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pause_seconds JSONB")
        op.execute("DROP INDEX IF EXISTS idx_voice_chunks_embedding")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE INDEX IF NOT EXISTS idx_voice_chunks_embedding {HNSW}")
        op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS pause_seconds")
        op.execute("ALTER TABLE api_usage DROP COLUMN IF EXISTS job_id")
        op.execute("ALTER TABLE api_usage DROP COLUMN IF EXISTS model")
