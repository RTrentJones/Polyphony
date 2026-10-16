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

    @pytest.mark.asyncio
    async def test_scenes_tab_is_in_book_order(
        self, client, auth_headers, async_session, test_user, book
    ):
        """SPEC-37: chapter position, then scene position; trashed scenes
        skipped; unfiled scenes last, newest first."""
        from datetime import datetime, timedelta, timezone
        from uuid import UUID

        from app.core.orm_models import Scene

        ids, scenes = [], []
        for title in ("A", "B"):
            r = await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": title},
                headers=auth_headers,
            )
            ids.append(r.json()["id"])
            for i in range(2):
                r = await client.post(
                    f"/api/v1/books/chapters/{ids[-1]}/scenes",
                    json={"title": f"{title}{i}"},
                    headers=auth_headers,
                )
                scenes.append(r.json()["scene_id"])
        await client.patch(
            f"/api/v1/books/chapters/{ids[1]}/position",
            json={"position": 0},
            headers=auth_headers,
        )
        await client.delete(f"/api/v1/scenes/{scenes[2]}", headers=auth_headers)
        now = datetime.now(timezone.utc)
        async_session.add_all(
            Scene(
                user_id=test_user.id,
                book_id=UUID(book["id"]),
                title=t,
                created_at=now - timedelta(minutes=age),
            )
            for t, age in (("old loose", 10), ("new loose", 1))
        )
        await async_session.commit()
        r = await client.get(f"/api/v1/books/{book['id']}/scenes", headers=auth_headers)
        titles = [s["title"] for s in r.json()["scenes"]]
        assert titles == ["B1", "A0", "A1", "new loose", "old loose"]


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

    @pytest.mark.asyncio
    async def test_saved_edit_survives_reload(
        self, client, auth_headers, book, chapter, async_session, test_user
    ):
        """GET scene and the scene list show the author's saved edit, not the
        generated draft (reloading the editor used to bring the draft back)."""
        from uuid import UUID

        from app.core.orm_models import Scene

        scene = Scene(
            user_id=test_user.id,
            book_id=UUID(book["id"]),
            chapter_id=UUID(chapter["id"]),
            position=0,
            status="completed",
            generated_content="Generated draft.",
        )
        async_session.add(scene)
        await async_session.commit()
        await async_session.refresh(scene)

        r = await client.put(
            f"/api/v1/books/scenes/{scene.id}/content",
            json={"content": "The author's edit."},
            headers=auth_headers,
        )
        assert r.status_code == 200

        got = await client.get(f"/api/v1/scenes/{scene.id}", headers=auth_headers)
        assert got.status_code == 200
        assert got.json()["content"] == "The author's edit."
        listed = await client.get("/api/v1/scenes/", headers=auth_headers)
        (row,) = [s for s in listed.json()["scenes"] if s["id"] == str(scene.id)]
        assert row["preview"] == "The author's edit."


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
        # "Chapter One" is the title's own number: the heading carries only
        # the book position, never "Chapter 1: Chapter One" (SPEC-21).
        assert "## Chapter 1\n" in r.text
        assert "Chapter One" not in r.text

    @pytest.mark.asyncio
    async def test_export_unknown_format(self, client, auth_headers, book):
        r = await client.get(
            f"/api/v1/books/{book['id']}/export?format=pdf", headers=auth_headers
        )
        assert r.status_code == 400

    async def _manuscript_book(self, client, auth_headers, book, async_session, user):
        """Five chapters with outline-numbered titles; 2 and 4 have no prose."""
        from uuid import UUID

        from app.core.orm_models import Scene

        prose = {1: ["Edric reviews.", "Milo closes."], 3: ["Ledger."], 5: ["End."]}
        for i in range(1, 6):
            r = await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": f"Chapter {i}: Part {i}"},
                headers=auth_headers,
            )
            assert r.status_code == 201
            for pos, text in enumerate(prose.get(i, [])):
                async_session.add(
                    Scene(
                        user_id=user.id,
                        chapter_id=UUID(r.json()["id"]),
                        position=pos,
                        status="completed",
                        content=text,
                    )
                )
        await async_session.commit()

    @staticmethod
    def _headings(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.startswith("## ")]

    @pytest.mark.asyncio
    async def test_export_is_a_clean_manuscript(
        self, client, auth_headers, book, async_session, test_user
    ):
        await self._manuscript_book(
            client, auth_headers, book, async_session, test_user
        )
        url = f"/api/v1/books/{book['id']}/export?format=md"

        r = await client.get(url, headers=auth_headers)
        assert r.status_code == 200
        assert self._headings(r.text) == [
            "## Chapter 1: Part 1",
            "## Chapter 3: Part 3",
            "## Chapter 5: Part 5",
        ]
        assert "Edric reviews.\n\n* * *\n\nMilo closes." in r.text

        r = await client.get(f"{url}&skip_empty=false", headers=auth_headers)
        assert len(self._headings(r.text)) == 5

        r = await client.get(f"{url}&from_chapter=3&to_chapter=4", headers=auth_headers)
        assert self._headings(r.text) == ["## Chapter 3: Part 3"]
        assert "Edric reviews." not in r.text

    @pytest.mark.asyncio
    async def test_export_rejects_an_inverted_range(self, client, auth_headers, book):
        r = await client.get(
            f"/api/v1/books/{book['id']}/export?format=md&from_chapter=4&to_chapter=2",
            headers=auth_headers,
        )
        assert r.status_code == 422
        r = await client.get(
            f"/api/v1/books/{book['id']}/export?format=md&from_chapter=0",
            headers=auth_headers,
        )
        assert r.status_code == 422


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

        assert promote.json()["suggested_characters"] == []

        detail = await client.get(f"/api/v1/books/{book['id']}", headers=auth_headers)
        chapters = detail.json()["chapters"]
        assert len(chapters) == 1
        # The beats are data now (SPEC-23), not lines in the summary.
        assert chapters[0]["summary"] == "Hero gets the call."
        assert [b["title"] for b in chapters[0]["planned_beats"]] == ["phone rings"]
        node_id = r.json()["content"][0]["children"][0]["node_id"]
        assert chapters[0]["planned_beats"][0]["node_id"] == node_id

        plans = await client.get(
            f"/api/v1/books/{book['id']}/plans", headers=auth_headers
        )
        assert plans.json()["plans"][0]["suggested_characters"] == []

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
