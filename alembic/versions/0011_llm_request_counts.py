"""llm_request_counts — the provider requests-per-day ledger (SPEC-03).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_request_counts (
            provider VARCHAR(50) NOT NULL,
            model VARCHAR(100) NOT NULL,
            day DATE NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (provider, model, day)
        )
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS llm_request_counts")
