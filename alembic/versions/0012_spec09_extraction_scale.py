"""Extraction at manuscript scale (SPEC-09): character aliases, source format,
extraction-run progress.

Revision ID: spec09_extraction_scale
Revises: 0011
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "spec09_extraction_scale"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE characters ADD COLUMN IF NOT EXISTS aliases JSON")
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS format VARCHAR(20)")
    op.execute("ALTER TABLE extraction_runs ADD COLUMN IF NOT EXISTS progress JSON")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE extraction_runs DROP COLUMN IF EXISTS progress")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS format")
    op.execute("ALTER TABLE characters DROP COLUMN IF EXISTS aliases")
