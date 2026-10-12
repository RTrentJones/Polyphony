"""SPEC-15 15b: small correctness defects (docs/REVIEW-2026-10.md F20).

One test (or pair) per defect: thread events stay inside the thread's book,
export leaves the synopsis out by default, deleting a character does not drop its
voice in a separate transaction, and promoting an outline node retries once on
a chapter-position conflict.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.orm_models import (
    Book,
    BookPlan,
    Chapter,
    Character,
    PlotThread,
    PlotThreadEvent,
    Scene,
    Source,
)


async def _add(session, obj):
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@pytest.mark.unit
class TestThreadEventScope:
    async def _setup(self, s, user, book):
        other = await _add(s, Book(user_id=user.id, title="Other Book"))
        thread = await _add(s, PlotThread(book_id=book.id, name="The heist"))
        ch = await _add(s, Chapter(book_id=book.id, title="One", position=0))
        other_ch = await _add(
            s, Chapter(book_id=other.id, title="Elsewhere", position=0)
        )
        return other, thread, ch, other_ch

    async def test_scene_and_chapter_in_the_threads_book_are_accepted(
        self, client, auth_headers, async_session, test_user, test_book, test_source
    ):
        _, thread, ch, _ = await self._setup(async_session, test_user, test_book)
        via_chapter = await _add(
            async_session, Scene(user_id=test_user.id, chapter_id=ch.id, title="a")
        )
        via_source = await _add(
            async_session,
            Scene(user_id=test_user.id, source_id=test_source.id, title="b"),
        )
        for scene in (via_chapter, via_source):
            resp = await client.post(
                f"/api/v1/threads/{thread.id}/events",
                json={
                    "note": "clue",
                    "scene_id": str(scene.id),
                    "chapter_id": str(ch.id),
                },
                headers=auth_headers,
            )
            assert resp.status_code == 201, resp.text

    async def test_chapter_from_another_book_is_404(
        self, client, auth_headers, async_session, test_user, test_book
    ):
        _, thread, _, other_ch = await self._setup(async_session, test_user, test_book)
        resp = await client.post(
            f"/api/v1/threads/{thread.id}/events",
            json={"note": "clue", "chapter_id": str(other_ch.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_scene_from_another_book_is_404(
        self, client, auth_headers, async_session, test_user, test_book
    ):
        other, thread, _, other_ch = await self._setup(
            async_session, test_user, test_book
        )
        other_src = await _add(
            async_session,
            Source(book_id=other.id, user_id=test_user.id, kind="upload", title="x"),
        )
        foreign = [
            Scene(user_id=test_user.id, chapter_id=other_ch.id, title="c"),
            Scene(user_id=test_user.id, source_id=other_src.id, title="d"),
            Scene(user_id=test_user.id, title="orphan"),  # no chapter, no source
        ]
        for scene in foreign:
            await _add(async_session, scene)
            resp = await client.post(
                f"/api/v1/threads/{thread.id}/events",
                json={"note": "clue", "scene_id": str(scene.id)},
                headers=auth_headers,
            )
            assert resp.status_code == 404, scene.title
        events = (await async_session.execute(select(PlotThreadEvent))).scalars().all()
        assert events == []


@pytest.mark.unit
class TestExportSynopsis:
    async def test_synopsis_left_out_by_default(self, client, auth_headers, test_book):
        resp = await client.get(
            f"/api/v1/books/{test_book.id}/export?format=md", headers=auth_headers
        )
        assert resp.status_code == 200
        assert "A test synopsis." not in resp.text
        assert "Test Book" in resp.text

    async def test_synopsis_included_on_request(self, client, auth_headers, test_book):
        resp = await client.get(
            f"/api/v1/books/{test_book.id}/export?format=md&include_synopsis=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "A test synopsis." in resp.text


@pytest.mark.unit
class TestDeleteCharacterOneTransaction:
    async def test_does_not_delete_voice_in_a_separate_session(
        self, client, auth_headers, async_session, test_character, monkeypatch
    ):
        """voice_chunks cascade with the row; the chunk store's own session (which
        commits independently) must not be used to delete them first."""
        import app.api.characters as characters_api

        class _Store:
            calls: list = []

            async def delete_character(self, character_id):
                self.calls.append(character_id)

        store = _Store()
        monkeypatch.setattr(characters_api, "get_chunk_store", lambda: store)

        cid = test_character.id
        resp = await client.delete(f"/api/v1/characters/{cid}", headers=auth_headers)
        assert resp.status_code == 204
        assert store.calls == []
        async_session.expire_all()
        assert (await async_session.get(Character, cid)) is None


@pytest.mark.unit
class TestPromoteRetriesPositionConflict:
    async def _outline(self, s, book):
        await _add(
            s,
            BookPlan(
                book_id=book.id,
                kind="outline",
                content=[{"title": "Opening", "summary": "It begins", "children": []}],
            ),
        )

    def _fail_commits(self, session, monkeypatch, times):
        real_commit = session.commit
        state = {"left": times}

        async def flaky_commit():
            if state["left"] > 0:
                state["left"] -= 1
                raise IntegrityError("INSERT INTO chapters", {}, Exception("dup"))
            await real_commit()

        monkeypatch.setattr(session, "commit", flaky_commit)
        return state

    async def test_one_conflict_is_retried(
        self, client, auth_headers, async_session, test_book, monkeypatch
    ):
        await self._outline(async_session, test_book)
        book_id = test_book.id  # the rollback inside the endpoint expires it
        state = self._fail_commits(async_session, monkeypatch, times=1)
        resp = await client.post(
            f"/api/v1/books/{book_id}/plans/promote",
            json={"node_index": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert state["left"] == 0
        assert resp.json()["title"] == "Opening"
        chapters = (
            (
                await async_session.execute(
                    select(Chapter).where(Chapter.book_id == book_id)
                )
            )
            .scalars()
            .all()
        )
        assert [c.title for c in chapters] == ["Opening"]

    async def test_second_conflict_is_409(
        self, client, auth_headers, async_session, test_book, monkeypatch
    ):
        await self._outline(async_session, test_book)
        self._fail_commits(async_session, monkeypatch, times=2)
        resp = await client.post(
            f"/api/v1/books/{test_book.id}/plans/promote",
            json={"node_index": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 409
