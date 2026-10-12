"""continuity_reports.coverage — which sections a check actually covered (SPEC-02).

A continuity chunk whose model output could not be parsed used to become `[]`,
indistinguishable from "no contradictions". Reports now carry
{chunks_checked, chunks_total, unchecked} and the status `incomplete` when a
section was not checked.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE continuity_reports ADD COLUMN IF NOT EXISTS coverage JSON")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE continuity_reports DROP COLUMN IF EXISTS coverage")
