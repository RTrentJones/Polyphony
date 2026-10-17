"""Refresh-token families and an explicit end (the refresh grace window).

`refresh_tokens.family_id` (UUID, nullable) groups one login's rotation lineage
so logout can end the whole family, siblings minted inside the grace window
included. `refresh_tokens.ended_at` (nullable) marks a token ended by logout or
theft revocation, which is never honoured; `revoked_at` now means only
"rotated". Existing rows keep NULLs: a pre-0020 token joins a new family on its
first rotation. Downgrade copies `ended_at` into `revoked_at` so the old code
still refuses a logged-out or theft-revoked token.

postgres-only and guarded: 0001 runs `create_all`, so a fresh database already
has the columns when this runs.

Revision ID: refresh_token_family
Revises: w2_planned_beats
Create Date: 2026-10-17

"""

from typing import Sequence, Union

from alembic import op

revision: str = "refresh_token_family"
down_revision: Union[str, None] = "w2_planned_beats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS family_id UUID")
    op.execute(
        "ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS "
        "ended_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family_id "
        "ON refresh_tokens (family_id)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS idx_refresh_tokens_family_id")
    # Pre-0020 code knows only revoked_at: keep logout/theft ends refused.
    op.execute(
        "UPDATE refresh_tokens SET revoked_at = COALESCE(revoked_at, ended_at) "
        "WHERE ended_at IS NOT NULL"
    )
    op.execute("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS ended_at")
    op.execute("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS family_id")
