"""Consolidated baseline schema.

Polyphony was never deployed before this consolidation, so the baseline simply
materializes the current ORM metadata (users + invites + refresh tokens,
manuscripts, characters, chunks, scenes, beats, api_usage). Subsequent
migrations are normal incremental Alembic revisions.

Revision ID: 0001
Revises:
Create Date: 2026-07-10

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.database import Base
    from app.core import orm_models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    # Vector search lives in the same Postgres via pgvector (ADR-001 amendment).
    # The voice_chunks table is deliberately NOT on the ORM Base — sqlite test
    # databases can't create a vector column — so it exists only here, guarded
    # to the postgresql dialect (Neon ships the extension on the free tier).
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_chunks (
              id UUID PRIMARY KEY,
              character_id UUID NOT NULL
                REFERENCES characters(id) ON DELETE CASCADE,
              user_id UUID NOT NULL,
              book_id UUID,
              chunk_type VARCHAR(50) NOT NULL,
              text TEXT NOT NULL,
              source_location VARCHAR(500),
              word_count INTEGER DEFAULT 0,
              embedding vector(384) NOT NULL,
              created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_chunks_character "
            "ON voice_chunks (character_id, chunk_type)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_chunks_embedding "
            "ON voice_chunks USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    from app.core.database import Base
    from app.core import orm_models  # noqa: F401

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS voice_chunks")
    Base.metadata.drop_all(bind=bind)
