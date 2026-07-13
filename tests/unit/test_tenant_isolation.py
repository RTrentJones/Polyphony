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

    async def test_extracted_character_reachable_via_manuscript(
        self, async_session, monkeypatch
    ):
        """Extracted rows carry user_id and stay joinable via their manuscript."""
        owner = await _user(async_session, "owner@example.com")
        ms = Manuscript(user_id=owner.id, title="M", content_hash="h1")
        async_session.add(ms)
        await async_session.commit()
        await async_session.refresh(ms)
        async_session.add(
            Character(user_id=owner.id, manuscript_id=ms.id, name="Jonathan")
        )
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


@pytest.mark.unit
class TestOwnershipNotNull:
    """The DB itself must reject tenant-owned rows without an owner."""

    async def test_character_requires_user_id(self, async_session):
        from sqlalchemy.exc import IntegrityError

        async_session.add(Character(name="Ownerless"))
        with pytest.raises(IntegrityError):
            await async_session.commit()
        await async_session.rollback()

    async def test_scene_requires_user_id(self, async_session):
        from sqlalchemy.exc import IntegrityError

        from app.core.orm_models import Scene

        async_session.add(Scene(title="Ownerless", position=0))
        with pytest.raises(IntegrityError):
            await async_session.commit()
        await async_session.rollback()


@pytest.mark.unit
class TestCrossTenantWritesFail:
    """Another tenant's move/edit attempts must 404, never mutate."""

    @pytest.fixture
    async def attacker_headers(self, async_session):
        # Direct DB user + minted token: registering through the API would
        # eat the register endpoint's 3/hour rate-limit budget shared by the
        # whole in-process test run.
        from app.core.security import create_access_token

        attacker = await _user(async_session, "attacker@example.com")
        token = create_access_token(data={"sub": str(attacker.id)})
        return {"Authorization": f"Bearer {token}"}

    @pytest.fixture
    async def victim_scene(self, client, auth_headers, async_session, test_user):
        from uuid import UUID

        from app.core.orm_models import Scene

        book = (
            await client.post(
                "/api/v1/books/", json={"title": "V"}, headers=auth_headers
            )
        ).json()
        chapter = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C1"},
                headers=auth_headers,
            )
        ).json()
        scene = Scene(
            user_id=test_user.id,
            chapter_id=UUID(chapter["id"]),
            position=0,
            status="completed",
            content="Victim prose.",
        )
        async_session.add(scene)
        await async_session.commit()
        await async_session.refresh(scene)
        return {"scene": scene, "chapter_id": chapter["id"]}

    async def test_cross_tenant_move_chapter_404(
        self, client, attacker_headers, victim_scene
    ):
        r = await client.patch(
            f"/api/v1/books/chapters/{victim_scene['chapter_id']}/position",
            json={"position": 0},
            headers=attacker_headers,
        )
        assert r.status_code == 404

    async def test_cross_tenant_move_scene_404(
        self, client, attacker_headers, victim_scene
    ):
        r = await client.patch(
            f"/api/v1/books/scenes/{victim_scene['scene'].id}/position",
            json={"position": 0},
            headers=attacker_headers,
        )
        assert r.status_code == 404

    async def test_cross_tenant_edit_scene_404(
        self, client, attacker_headers, victim_scene, async_session
    ):
        scene = victim_scene["scene"]
        r = await client.put(
            f"/api/v1/books/scenes/{scene.id}/content",
            json={"content": "Attacker prose."},
            headers=attacker_headers,
        )
        assert r.status_code == 404
        await async_session.refresh(scene)
        assert scene.content == "Victim prose."
