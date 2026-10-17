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


async def test_logout_of_a_pre_0020_token_waits_for_a_concurrent_rotation():
    """A pre-0020 token (no family): a refresh that loaded it first assigns a
    family and a successor; the logout waits for it and ends that family."""
    from sqlalchemy import update

    from app.core.database import get_async_db_url
    from app.core.orm_models import RefreshToken, User
    from app.core.security import (
        _hash_refresh_token,
        get_password_hash,
        issue_refresh_token,
        revoke_refresh_token,
        rotate_refresh_token,
    )

    engine = create_async_engine(get_async_db_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as s:
            user = User(
                email=f"legacy-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password=get_password_hash("longenoughpassword"),
                is_active=True,
            )
            s.add(user)
            await s.commit()
            t0 = await issue_refresh_token(s, user)
            await s.execute(
                update(RefreshToken)
                .where(RefreshToken.token_hash == _hash_refresh_token(t0))
                .values(family_id=None)
            )
            await s.commit()

        async with sessions() as refresh, sessions() as logout:
            _, t1 = await rotate_refresh_token(refresh, t0)  # holds t0's lock
            leaver = asyncio.create_task(revoke_refresh_token(logout, t0))
            await asyncio.sleep(0.5)
            assert not leaver.done()  # blocked behind the rotation
            await refresh.commit()
            await leaver
            await logout.commit()

        async with sessions() as s:
            assert await rotate_refresh_token(s, t1) is None
    finally:
        await engine.dispose()


async def test_downgrade_keeps_ended_tokens_refused():
    """Rolling 0020 back copies ended_at into revoked_at, so the old code still
    refuses a logged-out token."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.core.database import get_async_db_url

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_async_engine(get_async_db_url())
    uid, tid = uuid.uuid4(), uuid.uuid4()
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active) "
                    "VALUES (:u, :e, 'x', true)"
                ),
                {"u": uid, "e": f"down-{uid.hex[:8]}@example.com"},
            )
            await c.execute(
                text(
                    "INSERT INTO refresh_tokens (id, user_id, token_hash, "
                    "expires_at, ended_at) VALUES (:t, :u, :h, "
                    "now() + interval '30 days', now())"
                ),
                {"t": tid, "u": uid, "h": uuid.uuid4().hex * 2},
            )
        await asyncio.to_thread(command.downgrade, cfg, "-1")
        async with engine.connect() as c:
            revoked = (
                await c.execute(
                    text("SELECT revoked_at FROM refresh_tokens WHERE id = :t"),
                    {"t": tid},
                )
            ).scalar()
        assert revoked is not None
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        await engine.dispose()


async def test_theft_revocation_waits_for_another_familys_rotation():
    """Two logins of one user: a refresh in family B holds the user's lock while
    a replay in family A is detected as theft; the revocation waits and then
    ends B's new successor too."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.core.database import get_async_db_url
    from app.core.orm_models import RefreshToken, User
    from app.core.security import (
        _hash_refresh_token,
        get_password_hash,
        issue_refresh_token,
        rotate_refresh_token,
    )

    engine = create_async_engine(get_async_db_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as s:
            user = User(
                email=f"two-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password=get_password_hash("longenoughpassword"),
                is_active=True,
            )
            s.add(user)
            await s.commit()
            a0 = await issue_refresh_token(s, user)  # login A
            b0 = await issue_refresh_token(s, user)  # login B
            await s.commit()
            await rotate_refresh_token(s, a0)
            await s.commit()
            await s.execute(  # A's rotation is long past the grace window
                update(RefreshToken)
                .where(RefreshToken.token_hash == _hash_refresh_token(a0))
                .values(revoked_at=datetime.now(timezone.utc) - timedelta(minutes=5))
            )
            await s.commit()

        async with sessions() as b, sessions() as thief:
            _, b1 = await rotate_refresh_token(b, b0)  # holds the user's lock
            replay = asyncio.create_task(rotate_refresh_token(thief, a0))
            await asyncio.sleep(0.5)
            assert not replay.done()  # theft handling waits for B
            await b.commit()
            assert await replay is None
            await thief.commit()

        async with sessions() as s:
            assert await rotate_refresh_token(s, b1) is None  # B's successor ended
    finally:
        await engine.dispose()
