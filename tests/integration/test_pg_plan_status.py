"""SPEC-10 on real Postgres: every plan status the code writes fits the column.

SQLite ignores VARCHAR lengths, so the unit suite could not see that
'awaiting_confirmation' (21 characters) overflowed VARCHAR(20); the stub e2e
did. This pins it on the database the product runs on.

Skipped unless RUN_PG_TESTS is set (CI provides a pgvector service).
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUN_PG_TESTS"),
        reason="Postgres integration tests only run when RUN_PG_TESTS is set",
    ),
]

PLAN_STATUSES = (
    "ready",
    "generating",
    "paused",
    "awaiting_confirmation",
    "failed",
    "cancelled",
)


async def test_every_plan_status_fits_the_migrated_column():
    from alembic import command
    from alembic.config import Config

    from app.core.database import get_async_db_url

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    from app.core.orm_models import Book, BookPlan, User

    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            user = User(email=f"plan-{uuid.uuid4()}@ex.com", hashed_password="x")
            s.add(user)
            await s.flush()
            book = Book(user_id=user.id, title="B")
            s.add(book)
            await s.flush()
            plan = BookPlan(book_id=book.id, kind="outline", content=[])
            s.add(plan)
            await s.flush()
            for status in PLAN_STATUSES:
                plan.status = status
                await s.flush()
            await s.delete(user)
            await s.commit()
    finally:
        await engine.dispose()
