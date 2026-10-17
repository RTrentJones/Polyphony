"""Unit tests for invite-gated registration and refresh-token rotation."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.auth import _consume_invite
from app.core.orm_models import InviteCode, RefreshToken, User
from app.core.security import (
    REFRESH_COOKIE_NAME,
    _hash_refresh_token,
    get_password_hash,
    issue_refresh_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
)


async def _age_rotation(session, raw_hash_of, seconds):
    """Pretend the token was rotated `seconds` ago."""
    stored = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == raw_hash_of)
        )
    ).scalar_one()
    stored.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    await session.commit()


@pytest.fixture
async def invite(async_session):
    code = InviteCode(code="test-invite", max_uses=2)
    async_session.add(code)
    await async_session.commit()
    await async_session.refresh(code)
    return code


@pytest.mark.unit
class TestInviteConsumption:
    @pytest.mark.asyncio
    async def test_valid_invite_increments_uses(self, async_session, invite):
        consumed = await _consume_invite(async_session, "test-invite")
        assert consumed.uses == 1

    @pytest.mark.asyncio
    async def test_unknown_code_403(self, async_session):
        with pytest.raises(HTTPException) as exc:
            await _consume_invite(async_session, "nope")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_exhausted_code_403(self, async_session, invite):
        invite.uses = invite.max_uses
        await async_session.commit()
        with pytest.raises(HTTPException) as exc:
            await _consume_invite(async_session, "test-invite")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_code_403(self, async_session, invite):
        invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await async_session.commit()
        with pytest.raises(HTTPException) as exc:
            await _consume_invite(async_session, "test-invite")
        assert exc.value.status_code == 403


@pytest.fixture
async def user(async_session):
    u = User(
        email="refresh@example.com",
        hashed_password=get_password_hash("longenoughpassword"),
        is_active=True,
    )
    async_session.add(u)
    await async_session.commit()
    await async_session.refresh(u)
    return u


@pytest.mark.unit
class TestRefreshRotation:
    @pytest.mark.asyncio
    async def test_rotation_returns_new_token(self, async_session, user):
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()

        rotated = await rotate_refresh_token(async_session, raw)
        assert rotated is not None
        rotated_user, new_raw = rotated
        assert rotated_user.id == user.id
        assert new_raw != raw

    @pytest.mark.asyncio
    async def test_reuse_of_rotated_token_revokes_family(self, async_session, user):
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        rotated = await rotate_refresh_token(async_session, raw)
        assert rotated is not None
        await async_session.commit()
        await _age_rotation(async_session, _hash_refresh_token(raw), 31)

        # Replaying the old token after the grace = theft signal -> all revoked
        assert await rotate_refresh_token(async_session, raw) is None
        await async_session.commit()
        assert await rotate_refresh_token(async_session, rotated[1]) is None
        await async_session.commit()

        live = (
            (
                await async_session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == []

    @pytest.mark.asyncio
    async def test_a_lost_rotation_is_graced(self, async_session, user):
        """An aborted /refresh leaves the old cookie: within the grace window its
        reuse gets a fresh token and the session survives."""
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        first = await rotate_refresh_token(async_session, raw)
        await async_session.commit()
        again = await rotate_refresh_token(async_session, raw)
        await async_session.commit()
        assert again is not None and again[1] != first[1]
        assert await rotate_refresh_token(async_session, again[1]) is not None

    @pytest.mark.asyncio
    async def test_reuse_after_natural_expiry_still_revokes(self, async_session, user):
        """A token rotated just before it expired, replayed after both its expiry
        and the grace window, is still reuse: the successor dies too."""
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        rotated = await rotate_refresh_token(async_session, raw)
        await async_session.commit()
        stored = (
            await async_session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == _hash_refresh_token(raw)
                )
            )
        ).scalar_one()
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await async_session.commit()
        await _age_rotation(async_session, _hash_refresh_token(raw), 31)

        assert await rotate_refresh_token(async_session, raw) is None
        await async_session.commit()
        assert await rotate_refresh_token(async_session, rotated[1]) is None

    @pytest.mark.asyncio
    async def test_logout_ends_the_graceable_predecessor(self, async_session, user):
        """T0 -> T1, logout with T1 inside the window: T0 must not mint T2."""
        t0 = await issue_refresh_token(async_session, user)
        await async_session.commit()
        _, t1 = await rotate_refresh_token(async_session, t0)
        await async_session.commit()
        await revoke_refresh_token(async_session, t1)
        await async_session.commit()
        assert await rotate_refresh_token(async_session, t0) is None

    @pytest.mark.asyncio
    async def test_logout_ends_a_sibling_minted_in_the_window(
        self, async_session, user
    ):
        """Two tabs refresh T0 inside the window (T1, T1'); logout with T1 must
        also end T1'."""
        t0 = await issue_refresh_token(async_session, user)
        await async_session.commit()
        _, t1 = await rotate_refresh_token(async_session, t0)
        await async_session.commit()
        _, t1b = await rotate_refresh_token(async_session, t0)
        await async_session.commit()
        await revoke_refresh_token(async_session, t1)
        await async_session.commit()
        assert await rotate_refresh_token(async_session, t1b) is None

    @pytest.mark.asyncio
    async def test_the_window_holds_past_natural_expiry(self, async_session, user):
        """Rotated in its last seconds, retried just after expiry: still graced."""
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        await rotate_refresh_token(async_session, raw)
        await async_session.commit()
        stored = (
            await async_session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == _hash_refresh_token(raw)
                )
            )
        ).scalar_one()
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await async_session.commit()
        assert await rotate_refresh_token(async_session, raw) is not None

    @pytest.mark.asyncio
    async def test_a_rotated_pre_0020_token_is_never_graced(self, async_session, user):
        """No family to end on logout, so no grace: reuse is theft, as before."""
        raw = await issue_refresh_token(async_session, user)
        live = await issue_refresh_token(async_session, user)
        await async_session.commit()
        stored = (
            await async_session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == _hash_refresh_token(raw)
                )
            )
        ).scalar_one()
        stored.family_id = None
        stored.revoked_at = datetime.now(timezone.utc)
        await async_session.commit()
        assert await rotate_refresh_token(async_session, raw) is None
        await async_session.commit()
        assert await rotate_refresh_token(async_session, live) is None

    @pytest.mark.asyncio
    async def test_theft_detected_by_the_endpoint_is_kept(self, async_session, user):
        """/refresh answers 401 on theft; the revocation must survive the
        rollback that the 401 triggers in get_db."""
        import httpx

        from app.core.database import get_db
        from app.main import app

        async def real_get_db():  # get_db's semantics on the test session
            try:
                yield async_session
                await async_session.commit()
            except Exception:
                await async_session.rollback()
                raise

        t0 = await issue_refresh_token(async_session, user)
        await async_session.commit()
        _, t1 = await rotate_refresh_token(async_session, t0)
        await async_session.commit()
        await _age_rotation(async_session, _hash_refresh_token(t0), 31)

        app.dependency_overrides[get_db] = real_get_db
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                r = await c.post(
                    "/api/v1/auth/refresh", cookies={REFRESH_COOKIE_NAME: t0}
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert r.status_code == 401
        assert await rotate_refresh_token(async_session, t1) is None

    @pytest.mark.asyncio
    async def test_logout_and_theft_are_never_graced(self, async_session, user):
        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        await revoke_refresh_token(async_session, raw)  # logout
        await async_session.commit()
        assert await rotate_refresh_token(async_session, raw) is None

        raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        await rotate_refresh_token(async_session, raw)
        await revoke_all_refresh_tokens(async_session, user.id)  # theft elsewhere
        await async_session.commit()
        assert await rotate_refresh_token(async_session, raw) is None

    @pytest.mark.asyncio
    async def test_family_revocation_leaves_expired_rows_alone(
        self, async_session, user
    ):
        old_raw = await issue_refresh_token(async_session, user)
        live_raw = await issue_refresh_token(async_session, user)
        await async_session.commit()
        rows = {
            t.token_hash: t
            for t in (
                await async_session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == user.id)
                )
            ).scalars()
        }
        past = datetime.now(timezone.utc) - timedelta(days=1)
        rows[_hash_refresh_token(old_raw)].expires_at = past
        await async_session.commit()

        await revoke_all_refresh_tokens(async_session, user.id)
        await async_session.commit()
        old_row = rows[_hash_refresh_token(old_raw)]
        await async_session.refresh(old_row)
        assert old_row.ended_at is None  # untouched
        assert await rotate_refresh_token(async_session, live_raw) is None

    @pytest.mark.asyncio
    async def test_garbage_token_rejected(self, async_session, user):
        assert await rotate_refresh_token(async_session, "not-a-token") is None

    @pytest.mark.asyncio
    async def test_inactive_user_rejected(self, async_session, user):
        raw = await issue_refresh_token(async_session, user)
        user.is_active = False
        await async_session.commit()
        assert await rotate_refresh_token(async_session, raw) is None

    @pytest.mark.asyncio
    async def test_revoke_all(self, async_session, user):
        await issue_refresh_token(async_session, user)
        await issue_refresh_token(async_session, user)
        await async_session.commit()
        await revoke_all_refresh_tokens(async_session, user.id)
        await async_session.commit()
        live = (
            (
                await async_session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == []
