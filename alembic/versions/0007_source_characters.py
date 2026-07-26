"""source_characters: the many-to-many source<->character cast (PR review #2).

`Character.source_id` records only a character's FIRST source and goes NULL when
that file is deleted, so it cannot answer "which characters belong to this
source's cast?". A character re-proposed and merged from a later source has
source_id=None yet is genuinely part of that source. This association is the real
answer, written for every reviewed-commit character, so source-detail and the
source-scoped generation picker reach merged/multi-source characters.

Additive on the frozen baseline. **No backfill from `characters.source_id`** by
deployment decision: this change ships with a full DB reset (drop + recreate),
consistent with "live data is disposable" (docs/ADR-002-book-as-root.md). On the
reset databases `characters` is empty when this runs, so a backfill would be a
no-op; existing casts are (re)linked when their sources are re-extracted and
committed. If a future change must preserve live data, add:
`INSERT INTO source_characters (source_id, character_id)
 SELECT source_id, id FROM characters WHERE source_id IS NOT NULL
 ON CONFLICT DO NOTHING;`

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-25

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATEMENTS: list[str] = [
    """
    CREATE TABLE source_characters (
        source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        PRIMARY KEY (source_id, character_id)
    )
    """,
    "CREATE INDEX idx_source_characters_character_id "
    "ON source_characters (character_id)",
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
    op.execute("DROP TABLE IF EXISTS source_characters")
