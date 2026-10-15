"""AI revision proposals on scene revisions (SPEC-19, backlog B3).

`scene_revisions.meta` (JSONB, nullable) carries a proposal's instruction, the
range it revised, its job and its status (proposed / accepted / discarded /
failed / cancelled). Ordinary snapshots leave it NULL.

postgres-only and guarded: 0001 runs `create_all`, so a fresh database already
has the column when this runs.

Revision ID: b3_scene_revise
Revises: spec10_outline_handoff
Create Date: 2026-10-15

"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3_scene_revise"
down_revision: Union[str, None] = "spec10_outline_handoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE scene_revisions ADD COLUMN IF NOT EXISTS meta JSONB")


def downgrade() -> None:
    # Proposals stay as plain revisions (their text is kept); only the
    # instruction/status metadata goes.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE scene_revisions DROP COLUMN IF EXISTS meta")
