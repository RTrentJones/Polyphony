"""Canon entities: canon_entries + style_guides (Phase 3).

Book was the root but Character was the only canon entity, so worldbuilding had
nowhere to live but the synopsis field (docs/BRD.md R3). Additive on the frozen
baseline — no overlap guards.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    """
    CREATE TABLE canon_entries (
        id UUID PRIMARY KEY,
        book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        category VARCHAR(20) NOT NULL DEFAULT 'concept',
        content TEXT,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT uq_canon_entries_book_name UNIQUE (book_id, name)
    )
    """,
    "CREATE INDEX idx_canon_entries_book_id ON canon_entries (book_id)",
    """
    CREATE TABLE style_guides (
        id UUID PRIMARY KEY,
        book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        pov VARCHAR(50),
        tense VARCHAR(20),
        tone TEXT,
        comps TEXT,
        sample_prose TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT uq_style_guides_book UNIQUE (book_id)
    )
    """,
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # sqlite tests build the ORM shape via create_all
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS style_guides")
    op.execute("DROP TABLE IF EXISTS canon_entries")
