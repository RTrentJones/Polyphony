"""Manuscripts: store parsed text in the DB; scope content_hash per user.

- `content_text`: the parsed manuscript, so (re)processing is driven from
  Postgres instead of a container-local /tmp file that dies on restart.
- content_hash uniqueness moves from GLOBAL to per-user (drops the cross-tenant
  existence oracle and lets two users hold the same document).

Idempotent so it is safe whether the baseline built the old or the new shape.

Revision ID: 0003
Revises: 0002

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # sqlite tests build the current ORM shape from the 0001 baseline.
        return
    op.execute("ALTER TABLE manuscripts ADD COLUMN IF NOT EXISTS content_text TEXT")
    # Drop the global unique on content_hash (whatever it was named) and add the
    # per-user composite. Column-level unique=True yields *_content_hash_key.
    # DROP-then-ADD the composite too so this is idempotent whether the baseline
    # create_all already built it (fresh DB) or not (an older prod DB).
    op.execute(
        "ALTER TABLE manuscripts DROP CONSTRAINT IF EXISTS manuscripts_content_hash_key"
    )
    op.execute(
        "ALTER TABLE manuscripts DROP CONSTRAINT IF EXISTS uq_manuscripts_user_content"
    )
    op.execute(
        "ALTER TABLE manuscripts ADD CONSTRAINT uq_manuscripts_user_content "
        "UNIQUE (user_id, content_hash)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "ALTER TABLE manuscripts DROP CONSTRAINT IF EXISTS uq_manuscripts_user_content"
    )
    op.execute("ALTER TABLE manuscripts DROP COLUMN IF EXISTS content_text")
