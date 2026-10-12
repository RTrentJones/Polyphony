"""scenes.book_id — scenes are book-rooted (SPEC-04, closes F6).

A scene used to reach its book only through a nullable `chapter_id` with
`ON DELETE SET NULL`, so deleting a chapter or a book left its scenes, beats and
revisions as user-scoped orphans no book view could reach. Every scene now
carries `book_id NOT NULL` with `ON DELETE CASCADE` (docs/BRD.md R3.1).

Backfill: the chapter's book, else the source's book. A scene that resolves to
neither is unreachable through any book already; it is deleted (its beats and
revisions cascade) and the count is logged. There should be none in production.

Every statement is guarded, so a database that already has the column (or part
of it, after an interrupted run) converges on the same shape (ADR-002 §8).
postgres-only — sqlite tests build the ORM shape via create_all.

Revision ID: spec04_scene_book_id
Revises: 0011
Create Date: 2026-10-12

"""

import logging
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "spec04_scene_book_id"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# The name Postgres gives an unnamed column FK, so a later `create_all`-built
# database and a migrated one carry the same constraint name.
FK_NAME = "scenes_book_id_fkey"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE scenes ADD COLUMN IF NOT EXISTS book_id UUID")
    op.execute("""
        UPDATE scenes AS s SET book_id = c.book_id
        FROM chapters AS c
        WHERE s.book_id IS NULL AND s.chapter_id = c.id
        """)
    op.execute("""
        UPDATE scenes AS s SET book_id = src.book_id
        FROM sources AS src
        WHERE s.book_id IS NULL AND s.source_id = src.id
        """)
    orphans = bind.execute(text("DELETE FROM scenes WHERE book_id IS NULL")).rowcount
    if orphans:
        log.warning(
            "spec04_scene_book_id: deleted %d scene(s) reachable through no book",
            orphans,
        )
    op.execute("ALTER TABLE scenes ALTER COLUMN book_id SET NOT NULL")
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{FK_NAME}' AND conrelid = 'scenes'::regclass
            ) THEN
                ALTER TABLE scenes ADD CONSTRAINT {FK_NAME}
                    FOREIGN KEY (book_id) REFERENCES books (id) ON DELETE CASCADE;
            END IF;
        END $$
        """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_scenes_book_id ON scenes (book_id)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_scenes_book_id")
    op.execute(f"ALTER TABLE scenes DROP CONSTRAINT IF EXISTS {FK_NAME}")
    op.execute("ALTER TABLE scenes DROP COLUMN IF EXISTS book_id")
