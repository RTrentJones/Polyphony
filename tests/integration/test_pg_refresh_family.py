"""The refresh grace window on real Postgres: a graced rotation and a logout
of the same family are serialized (advisory lock), so a retry racing logout
cannot leave a live token behind.

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


async def test_a_grace_retry_waits_for_a_concurrent_logout():
    from alembic import command
    from alembic.config import Config

    from app.core.database import get_async_db_url
    from app.core.orm_models import User
    from app.core.security import (
        get_password_hash,
        issue_refresh_token,
        revoke_refresh_token,
        rotate_refresh_token,
    )

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_async_engine(get_async_db_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as s:
            user = User(
                email=f"race-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password=get_password_hash("longenoughpassword"),
                is_active=True,
            )
            s.add(user)
            await s.commit()
            t0 = await issue_refresh_token(s, user)
            await s.commit()
            _, t1 = await rotate_refresh_token(s, t0)
            await s.commit()

        async with sessions() as logout, sessions() as retry:
            await revoke_refresh_token(logout, t1)  # holds the family lock
            racer = asyncio.create_task(rotate_refresh_token(retry, t0))
            await asyncio.sleep(0.5)
            assert not racer.done()  # blocked behind logout
            await logout.commit()
            assert await racer is None  # sees the family ended
            await retry.commit()

        async with sessions() as s:
            assert await rotate_refresh_token(s, t1) is None
    finally:
        await engine.dispose()
