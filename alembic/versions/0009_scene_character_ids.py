"""scenes.character_ids — a scene's cast is character IDs, not free text (SPEC-01).

A scene's cast used to be a list of name strings typed by the author; a name
that matched no character silently produced voice-blind prose. The API now
resolves and validates the cast against the book, and stores the IDs here (the
display names stay in `scenes.characters`).

Additive and nullable (legacy rows keep names only). postgres-only — sqlite
tests build the ORM shape via create_all.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS character_ids JSON")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE scenes DROP COLUMN IF EXISTS character_ids")
