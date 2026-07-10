"""Characters: manuscript_id becomes optional.

Manual character creation (no manuscript upload) is a first-class origin, so
the FK to manuscripts must allow NULL. Ownership is carried by user_id.

Revision ID: 0002
Revises: 0001

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE characters ALTER COLUMN manuscript_id DROP NOT NULL")
    # sqlite (tests): the baseline materializes the current ORM metadata, which
    # already declares the column nullable — nothing to alter.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DELETE FROM characters WHERE manuscript_id IS NULL")
        op.execute("ALTER TABLE characters ALTER COLUMN manuscript_id SET NOT NULL")
