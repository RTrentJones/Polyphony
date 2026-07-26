"""Widen style_guides.pov / tense to TEXT (prod canon-save 500 fix).

`pov VARCHAR(50)` / `tense VARCHAR(20)` (migration 0003) were sized as if they
held short enum values, but the UI takes free-form prose. A descriptive POV
("first-person, past tense, close on the protagonist…") overflowed 50 chars and
Postgres raised StringDataRightTruncationError -> an opaque 500 on saving the
style guide. Widen both to TEXT, matching tone/comps/sample_prose. Widening is
lossless and needs no reset.

Additive on the frozen baseline. postgres-only (sqlite tests build the ORM shape,
already TEXT, via create_all).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    "ALTER TABLE style_guides ALTER COLUMN pov TYPE TEXT",
    "ALTER TABLE style_guides ALTER COLUMN tense TYPE TEXT",
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
    op.execute("ALTER TABLE style_guides ALTER COLUMN tense TYPE VARCHAR(20)")
    op.execute("ALTER TABLE style_guides ALTER COLUMN pov TYPE VARCHAR(50)")
