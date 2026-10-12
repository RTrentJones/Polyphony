"""outline -> chapter -> scene handoff: plan premise, node ids, chapter cast (SPEC-10).

Revision ID: spec10_outline_handoff
Revises: spec13_soft_delete
Create Date: 2026-10-12

"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "spec10_outline_handoff"
down_revision: Union[str, None] = "spec13_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _with_node_ids(nodes):
    """Give every stored node a node_id, so promote is idempotent for outlines
    generated before SPEC-10 too. Existing ids are kept."""
    if not isinstance(nodes, list):
        return nodes
    out = []
    for node in nodes:
        if isinstance(node, dict):
            node = dict(node)
            node.setdefault("node_id", str(uuid.uuid4()))
            node["children"] = _with_node_ids(node.get("children") or [])
        out.append(node)
    return out


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE book_plans ADD COLUMN IF NOT EXISTS premise JSON")
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS plan_node_id UUID")
    op.execute(
        "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS pov_character_id UUID "
        "REFERENCES characters(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS character_ids JSON")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapters_plan_node "
        "ON chapters (book_id, plan_node_id)"
    )
    rows = bind.execute(sa.text("SELECT id, content FROM book_plans")).fetchall()
    for plan_id, content in rows:
        if isinstance(content, str):
            content = json.loads(content)
        updated = _with_node_ids(content)
        if updated != content:
            bind.execute(
                sa.text(
                    "UPDATE book_plans SET content = CAST(:c AS json) WHERE id = :id"
                ),
                {"c": json.dumps(updated), "id": plan_id},
            )


def downgrade() -> None:
    # The node_ids added to book_plans.content stay: older code ignores them.
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_chapters_plan_node")
    for col in ("character_ids", "pov_character_id", "plan_node_id"):
        op.execute(f"ALTER TABLE chapters DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE book_plans DROP COLUMN IF EXISTS premise")
