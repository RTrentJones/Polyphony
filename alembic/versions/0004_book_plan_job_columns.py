"""BookPlan staged-generation columns: status/stage/warnings/error (Phase 5).

The outline becomes a background job (~6 LLM calls); these columns let the
frontend poll for stage progress and carry the fidelity audit's soft warnings.
Additive on the frozen baseline.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-24

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    "ALTER TABLE book_plans ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ready'",
    "ALTER TABLE book_plans ADD COLUMN stage VARCHAR(30)",
    "ALTER TABLE book_plans ADD COLUMN warnings JSON",
    "ALTER TABLE book_plans ADD COLUMN error TEXT",
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
    for col in ("status", "stage", "warnings", "error"):
        op.execute(f"ALTER TABLE book_plans DROP COLUMN IF EXISTS {col}")
