"""Enforce tenant ownership: characters/scenes user_id NOT NULL; usage index.

Backfills user_id on legacy rows by deriving the owner from the row's parent
(manuscript, book, or chapter->book), deletes the remainder (rows with no
derivable owner are unreachable through every owner-scoped API query path),
then makes the column NOT NULL so ownerless rows can never be inserted again.

Also indexes api_usage(user_id, timestamp) — the rolling-24h budget check
scans exactly that predicate on every LLM-spending request.

Revision ID: 0005
Revises: 0004

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # sqlite tests build the current ORM shape from the 0001 baseline.
        return

    # --- characters: derive owner, then enforce -------------------------------
    op.execute("""
        UPDATE characters c SET user_id = m.user_id
        FROM manuscripts m
        WHERE c.user_id IS NULL AND c.manuscript_id = m.id
        """)
    op.execute("""
        UPDATE characters c SET user_id = b.user_id
        FROM books b
        WHERE c.user_id IS NULL AND c.book_id = b.id
        """)
    orphan_chars = bind.execute(
        text("SELECT count(*) FROM characters WHERE user_id IS NULL")
    ).scalar()
    if orphan_chars:
        print(f"[0005] deleting {orphan_chars} ownerless character rows")
    # voice_chunks/character_chunks FKs cascade; explicit for the deploy log.
    op.execute(
        "DELETE FROM voice_chunks WHERE character_id IN "
        "(SELECT id FROM characters WHERE user_id IS NULL)"
    )
    op.execute(
        "DELETE FROM character_chunks WHERE character_id IN "
        "(SELECT id FROM characters WHERE user_id IS NULL)"
    )
    op.execute("DELETE FROM characters WHERE user_id IS NULL")
    op.execute("ALTER TABLE characters ALTER COLUMN user_id SET NOT NULL")

    # --- scenes: manuscript owner, else chapter->book owner -------------------
    op.execute("""
        UPDATE scenes s SET user_id = m.user_id
        FROM manuscripts m
        WHERE s.user_id IS NULL AND s.manuscript_id = m.id
        """)
    op.execute("""
        UPDATE scenes s SET user_id = b.user_id
        FROM chapters ch JOIN books b ON ch.book_id = b.id
        WHERE s.user_id IS NULL AND s.chapter_id = ch.id
        """)
    orphan_scenes = bind.execute(
        text("SELECT count(*) FROM scenes WHERE user_id IS NULL")
    ).scalar()
    if orphan_scenes:
        print(f"[0005] deleting {orphan_scenes} ownerless scene rows")
    op.execute("DELETE FROM scenes WHERE user_id IS NULL")
    op.execute("ALTER TABLE scenes ALTER COLUMN user_id SET NOT NULL")

    # --- budget-check hot path -------------------------------------------------
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_usage_user_time "
        "ON api_usage (user_id, timestamp)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Deleted orphans are gone for good; only the constraints are reversible.
    op.execute("ALTER TABLE characters ALTER COLUMN user_id DROP NOT NULL")
    op.execute("ALTER TABLE scenes ALTER COLUMN user_id DROP NOT NULL")
    op.execute("DROP INDEX IF EXISTS idx_api_usage_user_time")
