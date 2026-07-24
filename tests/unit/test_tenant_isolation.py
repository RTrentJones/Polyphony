"""Regression tests for cross-tenant isolation in generation-context assembly.

Book is the root now (docs/ADR-002-book-as-root.md §1): every character has a
NON-NULL `book_id`, and `load_characters_for_book` scopes strictly by
`(book_id, user_id)`. That closes the old leak structurally — there is no
NULL-scoped character to match across tenants — and the `user_id` guard remains
as defence in depth even when a `book_id` is known.
"""

import pytest

from app.characters.context import load_characters_for_book
from app.core.orm_models import Book, Character, User
from app.core.security import get_password_hash


async def _user(session, email: str) -> User:
    u = User(
        email=email, hashed_password=get_password_hash("password123"), full_name=email
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def _book(session, user: User, title: str) -> Book:
    b = Book(user_id=user.id, title=title)
    session.add(b)
    await session.commit()
    await session.refresh(b)
    return b


@pytest.mark.unit
class TestGenerationContextTenantScope:
    async def test_user_guard_blocks_another_tenant_even_with_book_id(
        self, async_session, monkeypatch
    ):
        """Defence in depth: even handed the victim's book_id, a wrong user gets nothing."""
        victim = await _user(async_session, "victim@example.com")
        attacker = await _user(async_session, "attacker@example.com")
        book = await _book(async_session, victim, "Victim's Book")
        async_session.add(
            Character(
                user_id=victim.id,
                book_id=book.id,
                name="Mina",
                description="secret bible",
            )
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

        found = await load_characters_for_book(
            ["Mina"], user_id=attacker.id, book_id=book.id
        )
        assert found == {}, "attacker must not resolve the victim's character"

        owned = await load_characters_for_book(
            ["Mina"], user_id=victim.id, book_id=book.id
        )
        assert "Mina" in owned, "owner must still resolve their own character"

    async def test_character_scoped_to_its_own_book(self, async_session, monkeypatch):
        """One character, one book: a name in book A is invisible when loading book B."""
        owner = await _user(async_session, "owner@example.com")
        book_a = await _book(async_session, owner, "Book A")
        book_b = await _book(async_session, owner, "Book B")
        async_session.add(
            Character(user_id=owner.id, book_id=book_a.id, name="Jonathan")
        )
        await async_session.commit()

        import app.characters.context as ctx

        class _Ctx:
            async def __aenter__(self_):
                return async_session

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(ctx, "get_async_session", lambda: _Ctx())

        in_a = await load_characters_for_book(
            ["Jonathan"], user_id=owner.id, book_id=book_a.id
        )
        assert "Jonathan" in in_a

        in_b = await load_characters_for_book(
            ["Jonathan"], user_id=owner.id, book_id=book_b.id
        )
        assert in_b == {}, "a character must not leak across the owner's own books"


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
