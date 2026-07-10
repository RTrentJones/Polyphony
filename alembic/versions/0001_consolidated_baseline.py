"""Consolidated baseline schema.

Polyphony was never deployed before this consolidation, so the baseline simply
materializes the current ORM metadata (users + invites + refresh tokens,
manuscripts, characters, chunks, scenes, beats, api_usage). Subsequent
migrations are normal incremental Alembic revisions.

Revision ID: 0001
Revises:
Create Date: 2026-07-10

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.database import Base
    from app.core import orm_models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.core.database import Base
    from app.core import orm_models  # noqa: F401

    Base.metadata.drop_all(bind=op.get_bind())
