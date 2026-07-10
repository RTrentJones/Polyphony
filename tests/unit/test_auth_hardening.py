"""Unit tests for invite-gated registration and refresh-token rotation."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.auth import _consume_invite
from app.core.orm_models import InviteCode, RefreshToken, User
from app.core.security import (
    get_password_hash,
    issue_refresh_token,
    revoke_all_refresh_tokens,
    rotate_refresh_token,
)


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

        # Replaying the old token = theft signal -> everything revoked
        assert await rotate_refresh_token(async_session, raw) is None
        await async_session.commit()

        live = (
            (
                await async_session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == []

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
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == []
