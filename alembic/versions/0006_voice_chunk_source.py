"""voice_chunks.source_id — voice provenance lives on the chunk (PR review #2).

A character can receive voice from more than one source, so provenance belongs on
the chunk, not on the character's single source_id. This lets voice indexing be
idempotent PER SOURCE (delete only this source's chunks before re-indexing)
without erasing a character's manual or prior-source voice. postgres-only, like
the rest of voice_chunks.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    "ALTER TABLE voice_chunks ADD COLUMN source_id UUID "
    "REFERENCES sources(id) ON DELETE SET NULL",
    "CREATE INDEX idx_voice_chunks_character_source "
    "ON voice_chunks (character_id, source_id)",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # voice_chunks is postgres-only (off the ORM Base)
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_voice_chunks_character_source")
    op.execute("ALTER TABLE voice_chunks DROP COLUMN IF EXISTS source_id")
