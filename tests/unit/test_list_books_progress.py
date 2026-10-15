"""GET /books/ carries each book's progress (SPEC-27, B12): words, chapters and
scenes drafted out of planned, and the last-edited scene for "Resume"."""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit


async def _list(client, auth_headers) -> dict:
    res = await client.get("/api/v1/books/", headers=auth_headers)
    assert res.status_code == 200
    return {b["id"]: b for b in res.json()["books"]}


class TestListBooksProgress:
    async def test_empty_book_has_zero_progress(self, client, auth_headers, test_book):
        book = (await _list(client, auth_headers))[str(test_book.id)]
        assert book["title"] == "Test Book"
        assert book["word_count"] == 0
        assert book["chapter_count"] == 0
        assert book["chapters_drafted"] == 0
        assert book["scenes_drafted"] == 0
        assert book["scenes_planned"] == 0
        assert book["last_edited_scene"] is None

    async def test_counts_and_last_edited(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import Chapter, Scene

        now = datetime.now(timezone.utc)
        planned = Chapter(
            book_id=test_book.id,
            title="One",
            position=0,
            summary="Opening.",
            planned_beats=[
                {"title": "Intake", "summary": "Milo arrives"},
                {"title": "Lunch"},
            ],
        )
        empty = Chapter(book_id=test_book.id, title="Two", position=1)
        trashed = Chapter(
            book_id=test_book.id,
            title="Gone",
            position=2,
            planned_beats=[{"title": "Ghost", "summary": "never counted"}],
            deleted_at=now,
        )
        async_session.add_all([planned, empty, trashed])
        await async_session.flush()

        def scene(**kw):
            base = dict(user_id=test_book.user_id, book_id=test_book.id)
            base.update(kw)
            return Scene(**base)

        async_session.add_all(
            [
                scene(
                    chapter_id=planned.id,
                    position=0,
                    title="Intake",
                    status="completed",
                    word_count=120,
                    updated_at=now - timedelta(hours=2),
                ),
                scene(
                    chapter_id=planned.id,
                    position=1,
                    title="Lunch",
                    status="completed",
                    word_count=80,
                    updated_at=now - timedelta(minutes=5),
                ),
                # A failed scene: no drafted credit, no words.
                scene(
                    chapter_id=empty.id,
                    position=0,
                    title="Broken",
                    status="failed",
                    word_count=0,
                    updated_at=now - timedelta(days=1),
                ),
                # Trashed: never counted, never resumed.
                scene(
                    chapter_id=planned.id,
                    position=2,
                    title="Trashed",
                    status="completed",
                    word_count=999,
                    deleted_at=now,
                    updated_at=now,
                ),
                # In a trashed chapter: not counted either.
                scene(
                    chapter_id=trashed.id,
                    position=0,
                    title="In trash",
                    status="completed",
                    word_count=500,
                    updated_at=now,
                ),
                # Standalone (no chapter): its words count.
                scene(
                    source_id=test_source.id,
                    position=0,
                    title="Loose",
                    status="completed",
                    word_count=30,
                    updated_at=now - timedelta(days=2),
                ),
            ]
        )
        await async_session.commit()

        book = (await _list(client, auth_headers))[str(test_book.id)]
        assert book["word_count"] == 230
        assert book["chapter_count"] == 2
        assert book["chapters_drafted"] == 1
        assert book["scenes_drafted"] == 3
        assert book["scenes_planned"] == 2
        last = book["last_edited_scene"]
        assert last["title"] == "Lunch"
        assert last["chapter_id"] == str(planned.id)
        assert last["chapter_title"] == "One"
        assert last["updated_at"]

    async def test_planned_count_reads_the_beats_only(
        self, client, auth_headers, async_session, test_book
    ):
        """The count is the stored beats (SPEC-23); a "Planned beats:" block
        typed into a summary is text and plans nothing."""
        from app.core.orm_models import Chapter

        async_session.add_all(
            [
                Chapter(
                    book_id=test_book.id,
                    title="One",
                    position=0,
                    planned_beats=[
                        {"title": "A"},
                        {"title": "B", "summary": "b"},
                        {"title": ""},  # untitled: dropped by normalize_planned_beats
                    ],
                ),
                Chapter(
                    book_id=test_book.id,
                    title="Two",
                    position=1,
                    summary="Notes.\n\nPlanned beats:\n- A: a\n- B: b",
                ),
            ]
        )
        await async_session.commit()
        book = (await _list(client, auth_headers))[str(test_book.id)]
        assert book["scenes_planned"] == 2

    async def test_books_are_isolated_per_book_and_user(
        self, client, auth_headers, async_session, test_book, test_user
    ):
        from app.core.orm_models import Book, Chapter, Scene, User

        other_book = Book(user_id=test_user.id, title="Second")
        stranger = User(email="x@example.com", hashed_password="x")
        async_session.add_all([other_book, stranger])
        await async_session.flush()
        foreign = Book(user_id=stranger.id, title="Not mine")
        async_session.add(foreign)
        await async_session.flush()
        ch = Chapter(book_id=other_book.id, title="Only", position=0)
        async_session.add(ch)
        await async_session.flush()
        async_session.add(
            Scene(
                user_id=test_user.id,
                book_id=other_book.id,
                chapter_id=ch.id,
                position=0,
                title="S",
                status="completed",
                word_count=42,
            )
        )
        await async_session.commit()

        books = await _list(client, auth_headers)
        assert str(foreign.id) not in books
        assert books[str(other_book.id)]["word_count"] == 42
        assert books[str(other_book.id)]["last_edited_scene"]["title"] == "S"
        assert books[str(test_book.id)]["word_count"] == 0
        assert books[str(test_book.id)]["last_edited_scene"] is None
