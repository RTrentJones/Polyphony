"""Integration tests for the book workflow API (sqlite + in-loop ASGI client)."""

import pytest


@pytest.fixture
async def book(client, auth_headers):
    response = await client.post(
        "/api/v1/books/",
        json={"title": "My Novel", "synopsis": "A story.", "genre": "fantasy"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
async def chapter(client, auth_headers, book):
    response = await client.post(
        f"/api/v1/books/{book['id']}/chapters",
        json={"title": "Chapter One", "summary": "It begins."},
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
class TestBooksCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get_book(self, client, auth_headers, book):
        response = await client.get(f"/api/v1/books/{book['id']}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "My Novel"
        assert data["chapters"] == []

    @pytest.mark.asyncio
    async def test_books_are_user_scoped(self, client, book, test_invite):
        # Second user can't see the first user's book
        register = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "otherpassword123",
                "invite_code": test_invite.code,
            },
        )
        token = register.json()["access_token"]
        response = await client.get(
            f"/api/v1/books/{book['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_chapter_reorder(self, client, auth_headers, book):
        ids = []
        for title in ("A", "B", "C"):
            r = await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": title},
                headers=auth_headers,
            )
            ids.append(r.json()["id"])
        # Move C to the front
        r = await client.patch(
            f"/api/v1/books/chapters/{ids[2]}/position",
            json={"position": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        detail = await client.get(f"/api/v1/books/{book['id']}", headers=auth_headers)
        titles = [c["title"] for c in detail.json()["chapters"]]
        assert titles == ["C", "A", "B"]


@pytest.mark.integration
class TestDraftEditing:
    @pytest.mark.asyncio
    async def test_edit_snapshots_revision(
        self, client, auth_headers, chapter, async_session, test_user
    ):
        from uuid import UUID

        from app.core.orm_models import Scene

        scene = Scene(
            user_id=test_user.id,
            chapter_id=UUID(chapter["id"]),
            position=0,
            status="completed",
            generated_content="Original prose.",
            content="Original prose.",
        )
        async_session.add(scene)
        await async_session.commit()
        await async_session.refresh(scene)

        r = await client.put(
            f"/api/v1/books/scenes/{scene.id}/content",
            json={"content": "Edited prose, much better."},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["word_count"] == 4

        revisions = await client.get(
            f"/api/v1/books/scenes/{scene.id}/revisions", headers=auth_headers
        )
        data = revisions.json()
        assert len(data["revisions"]) == 1
        assert data["revisions"][0]["content"] == "Original prose."


@pytest.mark.integration
class TestExportEndpoint:
    @pytest.mark.asyncio
    async def test_export_markdown(
        self, client, auth_headers, book, chapter, async_session, test_user
    ):
        from uuid import UUID

        from app.core.orm_models import Scene

        scene = Scene(
            user_id=test_user.id,
            chapter_id=UUID(chapter["id"]),
            position=0,
            status="completed",
            content="The story unfolds.",
        )
        async_session.add(scene)
        await async_session.commit()

        r = await client.get(
            f"/api/v1/books/{book['id']}/export?format=md", headers=auth_headers
        )
        assert r.status_code == 200
        assert "The story unfolds." in r.text
        assert "Chapter 1: Chapter One" in r.text

    @pytest.mark.asyncio
    async def test_export_unknown_format(self, client, auth_headers, book):
        r = await client.get(
            f"/api/v1/books/{book['id']}/export?format=pdf", headers=auth_headers
        )
        assert r.status_code == 400


@pytest.mark.integration
class TestPlansAPI:
    @pytest.mark.asyncio
    async def test_upsert_and_promote_outline(self, client, auth_headers, book):
        outline = [
            {
                "title": "The Call",
                "summary": "Hero gets the call.",
                "children": [{"title": "phone rings", "summary": "", "children": []}],
            },
        ]
        r = await client.put(
            f"/api/v1/books/{book['id']}/plans",
            json={"kind": "outline", "content": outline},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["content"][0]["title"] == "The Call"

        promote = await client.post(
            f"/api/v1/books/{book['id']}/plans/promote",
            json={"node_index": 0},
            headers=auth_headers,
        )
        assert promote.status_code == 200
        assert promote.json()["title"] == "The Call"

        detail = await client.get(f"/api/v1/books/{book['id']}", headers=auth_headers)
        chapters = detail.json()["chapters"]
        assert len(chapters) == 1
        assert "phone rings" in chapters[0]["summary"]

    @pytest.mark.asyncio
    async def test_threads_crud(self, client, auth_headers, book):
        r = await client.post(
            f"/api/v1/books/{book['id']}/threads",
            json={"name": "The Prophecy", "description": "It looms."},
            headers=auth_headers,
        )
        assert r.status_code == 201
        thread_id = r.json()["id"]

        event = await client.post(
            f"/api/v1/threads/{thread_id}/events",
            json={"note": "Prophecy introduced", "kind": "setup"},
            headers=auth_headers,
        )
        assert event.status_code == 201

        threads = await client.get(
            f"/api/v1/books/{book['id']}/threads", headers=auth_headers
        )
        data = threads.json()["threads"]
        assert data[0]["name"] == "The Prophecy"
        assert data[0]["events"][0]["kind"] == "setup"

    @pytest.mark.asyncio
    async def test_continuity_requires_prose(self, client, auth_headers, book):
        r = await client.post(
            f"/api/v1/books/{book['id']}/continuity",
            json={},
            headers=auth_headers,
        )
        assert r.status_code == 400  # no prose yet
