"""Drop character_chunks — the write-only mirror of voice_chunks (SPEC-11, F18).

`add_voice_samples` wrote every sample to both `voice_chunks` and
`character_chunks`, but chunk edit and delete touched only `voice_chunks` and
nothing ever read `character_chunks`, so it drifted after the first edit.
`voice_chunks` is the one store of a character's voice.

The downgrade recreates the empty table in its 0001 baseline shape; the rows it
held were a stale copy, so nothing is lost by not restoring them.

Revision ID: spec11_drop_character_chunks
Revises: spec09_extraction_scale
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "spec11_drop_character_chunks"
down_revision: Union[str, None] = "spec09_extraction_scale"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_character_chunks_character_id")
    op.execute("DROP TABLE IF EXISTS character_chunks")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE TABLE IF NOT EXISTS character_chunks (
            id UUID NOT NULL,
            character_id UUID NOT NULL,
            chunk_type VARCHAR(50),
            content TEXT NOT NULL,
            source_location VARCHAR(500),
            embedding_id VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
            PRIMARY KEY (id),
            FOREIGN KEY(character_id) REFERENCES characters (id) ON DELETE CASCADE
        )
        """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_character_chunks_character_id "
        "ON character_chunks (character_id)"
    )
