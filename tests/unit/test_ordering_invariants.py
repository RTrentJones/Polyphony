"""Ordering invariants: UNIQUE (parent, position) + reorder/create behavior.

The sqlite test schema carries the same UniqueConstraints as Postgres
(migration 0006), so these enforce the real invariant, not a convention.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.orm_models import Book, Chapter, Scene


@pytest.fixture
async def owned_book(async_session, test_user):
    book = Book(user_id=test_user.id, title="B")
    async_session.add(book)
    await async_session.commit()
    await async_session.refresh(book)
    return book


@pytest.mark.unit
class TestUniqueConstraints:
    async def test_duplicate_chapter_position_rejected(self, async_session, owned_book):
        async_session.add(Chapter(book_id=owned_book.id, title="A", position=0))
        await async_session.commit()
        async_session.add(Chapter(book_id=owned_book.id, title="B", position=0))
        with pytest.raises(IntegrityError):
            await async_session.commit()
        await async_session.rollback()

    async def test_duplicate_scene_position_in_chapter_rejected(
        self, async_session, test_user, owned_book
    ):
        chapter = Chapter(book_id=owned_book.id, title="A", position=0)
        async_session.add(chapter)
        await async_session.commit()
        async_session.add(
            Scene(user_id=test_user.id, chapter_id=chapter.id, position=0)
        )
        await async_session.commit()
        async_session.add(
            Scene(user_id=test_user.id, chapter_id=chapter.id, position=0)
        )
        with pytest.raises(IntegrityError):
            await async_session.commit()
        await async_session.rollback()

    async def test_standalone_scenes_may_share_position_zero(
        self, async_session, test_user
    ):
        async_session.add(Scene(user_id=test_user.id, position=0))
        async_session.add(Scene(user_id=test_user.id, position=0))
        await async_session.commit()  # NULL chapter_id is distinct


@pytest.fixture
async def book_via_api(client, auth_headers):
    r = await client.post("/api/v1/books/", json={"title": "N"}, headers=auth_headers)
    return r.json()


async def _make_chapters(client, auth_headers, book_id, titles):
    ids = []
    for title in titles:
        r = await client.post(
            f"/api/v1/books/{book_id}/chapters",
            json={"title": title},
            headers=auth_headers,
        )
        assert r.status_code == 201
        ids.append(r.json()["id"])
    return ids


async def _chapter_titles(client, auth_headers, book_id):
    detail = await client.get(f"/api/v1/books/{book_id}", headers=auth_headers)
    chapters = detail.json()["chapters"]
    assert [c["position"] for c in chapters] == list(range(len(chapters)))
    return [c["title"] for c in chapters]


@pytest.mark.unit
class TestChapterReorder:
    async def test_move_down(self, client, auth_headers, book_via_api):
        ids = await _make_chapters(
            client, auth_headers, book_via_api["id"], ["A", "B", "C"]
        )
        r = await client.patch(
            f"/api/v1/books/chapters/{ids[0]}/position",
            json={"position": 2},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert await _chapter_titles(client, auth_headers, book_via_api["id"]) == [
            "B",
            "C",
            "A",
        ]

    async def test_move_up(self, client, auth_headers, book_via_api):
        ids = await _make_chapters(
            client, auth_headers, book_via_api["id"], ["A", "B", "C"]
        )
        r = await client.patch(
            f"/api/v1/books/chapters/{ids[2]}/position",
            json={"position": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert await _chapter_titles(client, auth_headers, book_via_api["id"]) == [
            "C",
            "A",
            "B",
        ]

    async def test_noop_move(self, client, auth_headers, book_via_api):
        ids = await _make_chapters(
            client, auth_headers, book_via_api["id"], ["A", "B", "C"]
        )
        r = await client.patch(
            f"/api/v1/books/chapters/{ids[1]}/position",
            json={"position": 1},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert await _chapter_titles(client, auth_headers, book_via_api["id"]) == [
            "A",
            "B",
            "C",
        ]

    async def test_out_of_range_clamps_to_end(self, client, auth_headers, book_via_api):
        ids = await _make_chapters(
            client, auth_headers, book_via_api["id"], ["A", "B", "C"]
        )
        r = await client.patch(
            f"/api/v1/books/chapters/{ids[0]}/position",
            json={"position": 99},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert await _chapter_titles(client, auth_headers, book_via_api["id"]) == [
            "B",
            "C",
            "A",
        ]

    async def test_create_at_occupied_position_shifts_siblings(
        self, client, auth_headers, book_via_api
    ):
        await _make_chapters(client, auth_headers, book_via_api["id"], ["A", "B", "C"])
        r = await client.post(
            f"/api/v1/books/{book_via_api['id']}/chapters",
            json={"title": "X", "position": 1},
            headers=auth_headers,
        )
        assert r.status_code == 201
        assert r.json()["position"] == 1
        assert await _chapter_titles(client, auth_headers, book_via_api["id"]) == [
            "A",
            "X",
            "B",
            "C",
        ]

    async def test_create_rejects_negative_position(
        self, client, auth_headers, book_via_api
    ):
        r = await client.post(
            f"/api/v1/books/{book_via_api['id']}/chapters",
            json={"title": "X", "position": -1},
            headers=auth_headers,
        )
        assert r.status_code == 422


@pytest.mark.unit
class TestSceneReorder:
    @pytest.fixture
    async def chapter_with_scenes(
        self, client, auth_headers, book_via_api, async_session, test_user
    ):
        from uuid import UUID

        r = await client.post(
            f"/api/v1/books/{book_via_api['id']}/chapters",
            json={"title": "C1"},
            headers=auth_headers,
        )
        chapter_id = r.json()["id"]
        scene_ids = []
        for i, name in enumerate(("s0", "s1", "s2")):
            scene = Scene(
                user_id=test_user.id,
                chapter_id=UUID(chapter_id),
                position=i,
                title=name,
                status="completed",
                content=f"{name} prose",
            )
            async_session.add(scene)
            await async_session.commit()
            scene_ids.append(str(scene.id))
        return {"chapter_id": chapter_id, "scene_ids": scene_ids}

    async def _scene_order(self, client, auth_headers, chapter_id):
        detail = await client.get(
            f"/api/v1/books/chapters/{chapter_id}", headers=auth_headers
        )
        scenes = detail.json()["scenes"]
        assert [s["position"] for s in scenes] == list(range(len(scenes)))
        return [s["id"] for s in scenes]

    async def test_move_scene_to_front(self, client, auth_headers, chapter_with_scenes):
        ids = chapter_with_scenes["scene_ids"]
        r = await client.patch(
            f"/api/v1/books/scenes/{ids[2]}/position",
            json={"position": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["position"] == 0
        order = await self._scene_order(
            client, auth_headers, chapter_with_scenes["chapter_id"]
        )
        assert order == [ids[2], ids[0], ids[1]]

    async def test_move_scene_noop(self, client, auth_headers, chapter_with_scenes):
        ids = chapter_with_scenes["scene_ids"]
        r = await client.patch(
            f"/api/v1/books/scenes/{ids[1]}/position",
            json={"position": 1},
            headers=auth_headers,
        )
        assert r.status_code == 200
        order = await self._scene_order(
            client, auth_headers, chapter_with_scenes["chapter_id"]
        )
        assert order == ids


@pytest.mark.unit
class TestAppendRetry:
    async def test_create_chapter_retries_once_on_commit_conflict(
        self, client, auth_headers, book_via_api, async_session
    ):
        """First commit raises IntegrityError (simulated race); the endpoint
        must roll back, recompute the position, and succeed on attempt 2."""
        real_commit = async_session.commit
        state = {"armed": True}

        async def flaky_commit():
            if state["armed"]:
                state["armed"] = False
                await async_session.rollback()
                raise IntegrityError("INSERT INTO chapters", None, Exception("dup"))
            await real_commit()

        async_session.commit = flaky_commit
        try:
            r = await client.post(
                f"/api/v1/books/{book_via_api['id']}/chapters",
                json={"title": "A"},
                headers=auth_headers,
            )
        finally:
            async_session.commit = real_commit
        assert r.status_code == 201
        assert r.json()["position"] == 0
