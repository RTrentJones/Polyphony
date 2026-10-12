"""Soft delete and the Trash (SPEC-13, closes F16).

Characters, canon entries, sources, chapters and scenes gain `deleted_at`.
Deleting one sets it (after appending a `deleted` version); restoring clears it;
only an explicit purge removes the row. Every `(book_id, name)` /
`(book_id, content_hash)` / position uniqueness becomes a partial unique index
over live rows (`WHERE deleted_at IS NULL`), so a trashed name can be reused and
a trashed chapter does not hold its position.

Every statement is guarded, so a database built by `create_all` (which already
has the partial indexes) or an interrupted run converges on the same shape.
postgres-only — sqlite tests build the ORM shape via create_all.

Downgrade restores the full UNIQUE constraints. A trashed row would become live
again when the column goes (and could collide on those constraints), so trashed
rows are purged first; their content is still in `entity_versions`.

Revision ID: spec13_soft_delete
Revises: spec07_job_lifecycle
Create Date: 2026-10-12

"""

import logging
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "spec13_soft_delete"
down_revision: Union[str, None] = "spec07_job_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# Children before parents, so a downgrade purge never trips a FK.
TABLES = ("scenes", "chapters", "characters", "canon_entries", "sources")

# name -> (table, columns): the full constraints of 0001/0003, now partial.
UNIQUES = {
    "uq_sources_book_content": ("sources", "book_id, content_hash"),
    "uq_chapters_book_position": ("chapters", "book_id, position"),
    "uq_characters_book_name": ("characters", "book_id, name"),
    "uq_scenes_chapter_position": ("scenes", "chapter_id, position"),
    "uq_canon_entries_book_name": ("canon_entries", "book_id, name"),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "deleted_at TIMESTAMP WITH TIME ZONE"
        )
    for name, (table, columns) in UNIQUES.items():
        # The constraint owns an index of the same name; dropping the
        # constraint drops it, freeing the name for the partial index.
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({columns}) "
            "WHERE deleted_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in TABLES:
        has_column = bind.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = 'deleted_at'"
            ),
            {"t": table},
        ).first()
        if has_column:
            purged = bind.execute(
                text(f"DELETE FROM {table} WHERE deleted_at IS NOT NULL")
            ).rowcount
            if purged:
                log.warning(
                    "spec13_soft_delete downgrade: purged %d trashed %s row(s)",
                    purged,
                    table,
                )
    for name, (table, columns) in UNIQUES.items():
        op.execute(f"DROP INDEX IF EXISTS {name}")
        op.execute(f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}')
              THEN ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({columns});
              END IF;
            END $$
            """)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS deleted_at")
