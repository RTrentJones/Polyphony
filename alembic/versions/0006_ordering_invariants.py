"""Unique (parent, position) constraints for chapters and scenes.

Positions were managed purely by application convention; concurrent creates
or reorders could produce duplicates. Existing duplicates are renumbered
(stable order: position, created_at, id) before the constraints land.

Scenes are constrained only within a chapter: chapter_id NULL (standalone
scenes) is distinct in unique constraints, so any number of standalone
scenes may share position 0 — same semantics as sqlite's ORM-built schema.

Revision ID: 0006
Revises: 0005

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # sqlite tests build the current ORM shape from the 0001 baseline.
        return

    op.execute("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY book_id
                       ORDER BY position, created_at, id
                   ) - 1 AS rn
            FROM chapters
        )
        UPDATE chapters c SET position = r.rn
        FROM ranked r
        WHERE c.id = r.id AND c.position <> r.rn
        """)
    # DROP-then-ADD so this is idempotent whether the 0001 baseline create_all
    # already built the constraint (fresh DB) or not (an older prod DB).
    op.execute(
        "ALTER TABLE chapters DROP CONSTRAINT IF EXISTS uq_chapters_book_position"
    )
    op.execute(
        "ALTER TABLE chapters ADD CONSTRAINT uq_chapters_book_position "
        "UNIQUE (book_id, position)"
    )

    op.execute("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY chapter_id
                       ORDER BY position, created_at, id
                   ) - 1 AS rn
            FROM scenes
            WHERE chapter_id IS NOT NULL
        )
        UPDATE scenes s SET position = r.rn
        FROM ranked r
        WHERE s.id = r.id AND s.position <> r.rn
        """)
    op.execute(
        "ALTER TABLE scenes DROP CONSTRAINT IF EXISTS uq_scenes_chapter_position"
    )
    op.execute(
        "ALTER TABLE scenes ADD CONSTRAINT uq_scenes_chapter_position "
        "UNIQUE (chapter_id, position)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "ALTER TABLE chapters DROP CONSTRAINT IF EXISTS uq_chapters_book_position"
    )
    op.execute(
        "ALTER TABLE scenes DROP CONSTRAINT IF EXISTS uq_scenes_chapter_position"
    )
