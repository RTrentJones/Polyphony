"""Book-scoped scenes: a standalone (source-based) scene and a chapter scene both
surface via GET /books/{id}/scenes, so nothing generated is invisible."""

import pytest

pytestmark = pytest.mark.unit


class TestBookScenes:
    async def test_lists_chapter_and_standalone_scenes(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import Chapter, Scene

        chapter = Chapter(book_id=test_book.id, title="One", position=0)
        async_session.add(chapter)
        await async_session.flush()
        # filed in a chapter
        s_chap = Scene(
            user_id=test_book.user_id,
            chapter_id=chapter.id,
            position=0,
            title="Chap scene",
            status="completed",
            content="chapter prose",
            characters=["Milo"],
        )
        # standalone — reaches the book only through its source
        s_std = Scene(
            user_id=test_book.user_id,
            source_id=test_source.id,
            position=0,
            title="Standalone",
            status="completed",
            generated_content="loose prose",
            characters=["Zara"],
        )
        async_session.add_all([s_chap, s_std])
        await async_session.commit()

        scenes = (
            await client.get(
                f"/api/v1/books/{test_book.id}/scenes", headers=auth_headers
            )
        ).json()["scenes"]
        by_title = {s["title"]: s for s in scenes}
        assert {"Chap scene", "Standalone"} <= set(by_title)

        assert by_title["Chap scene"]["chapter_id"] == str(chapter.id)
        assert by_title["Chap scene"]["chapter_title"] == "One"

        # the previously-invisible standalone scene is now surfaced + readable
        assert by_title["Standalone"]["chapter_id"] is None
        assert by_title["Standalone"]["preview"] == "loose prose"
