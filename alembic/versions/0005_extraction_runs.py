"""Extraction runs: Source -> proposed Canon, held for review (Phase 6).

Additive on the frozen baseline.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-24

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    """
    CREATE TABLE extraction_runs (
        id UUID PRIMARY KEY,
        book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        source_id UUID REFERENCES sources(id) ON DELETE SET NULL,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        proposals JSON,
        error TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX idx_extraction_runs_book_id ON extraction_runs (book_id)",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS extraction_runs")
