"""Append-only version log for canon entities (Phase 2).

The first additive migration on top of the frozen book-as-root baseline — and it
needs NO overlap guards, which is the whole point of freezing 0001: the baseline
builds exactly its 17 tables and nothing else, so `entity_versions` is genuinely
new here (docs/ADR-002-book-as-root.md §5, §8).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    """
    CREATE TABLE entity_versions (
        id UUID PRIMARY KEY,
        book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        entity_type VARCHAR(50) NOT NULL,
        entity_id UUID NOT NULL,
        version_no INTEGER NOT NULL,
        content JSON NOT NULL,
        reason VARCHAR(255),
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT uq_entity_versions_ver UNIQUE (entity_type, entity_id, version_no)
    )
    """,
    "CREATE INDEX idx_entity_versions_lookup "
    "ON entity_versions (entity_type, entity_id, version_no DESC)",
    "CREATE INDEX idx_entity_versions_book_id ON entity_versions (book_id)",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Unit tests never run migrations — conftest builds the ORM shape (which
        # already includes entity_versions) via create_all.
        return
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS entity_versions")
