"""Regression tests for cross-tenant isolation in generation-context assembly.

These lock the fix for the leak where scene generation loaded characters by
name with no user scope (book_id-is-null matched every tenant's characters).
"""

import pytest

from app.characters.context import load_characters_for_book_or_manuscript
from app.core.orm_models import Character, Manuscript, User
from app.core.security import get_password_hash


async def _user(session, email: str) -> User:
    u = User(
        email=email, hashed_password=get_password_hash("password123"), full_name=email
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


@pytest.mark.unit
class TestGenerationContextTenantScope:
    async def test_does_not_load_another_users_character_by_name(
        self, async_session, monkeypatch
    ):
        """The exact leak: attacker names a victim's character; must get nothing."""
        victim = await _user(async_session, "victim@example.com")
        attacker = await _user(async_session, "attacker@example.com")
        async_session.add(
            Character(user_id=victim.id, name="Mina", description="secret bible")
        )
        await async_session.commit()

        # context assembly opens its own session — point it at the test session.
        import app.characters.context as ctx

        class _Ctx:
            async def __aenter__(self_):
                return async_session

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(ctx, "get_async_session", lambda: _Ctx())

        found = await load_characters_for_book_or_manuscript(
            ["Mina"], user_id=attacker.id
        )
        assert found == {}, "attacker must not resolve the victim's character"

        owned = await load_characters_for_book_or_manuscript(
            ["Mina"], user_id=victim.id
        )
        assert "Mina" in owned, "owner must still resolve their own character"

    async def test_legacy_extracted_character_reachable_via_manuscript(
        self, async_session, monkeypatch
    ):
        """Extracted rows (user_id set now, but also joinable via manuscript)."""
        owner = await _user(async_session, "owner@example.com")
        ms = Manuscript(user_id=owner.id, title="M", content_hash="h1")
        async_session.add(ms)
        await async_session.commit()
        await async_session.refresh(ms)
        # simulate a legacy extracted row: user_id NULL, owned via manuscript
        async_session.add(Character(manuscript_id=ms.id, name="Jonathan"))
        await async_session.commit()

        import app.characters.context as ctx

        class _Ctx:
            async def __aenter__(self_):
                return async_session

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(ctx, "get_async_session", lambda: _Ctx())

        owned = await load_characters_for_book_or_manuscript(
            ["Jonathan"], user_id=owner.id
        )
        assert "Jonathan" in owned

        other = await _user(async_session, "other@example.com")
        assert (
            await load_characters_for_book_or_manuscript(["Jonathan"], user_id=other.id)
            == {}
        )
