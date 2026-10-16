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
        # chapter-less — its book_id is derived from its source on insert (SPEC-04)
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

    async def test_get_scene_returns_its_beats_in_order(
        self, client, auth_headers, async_session, test_book
    ):
        """W1-S9: GET /scenes/{id} carries the beats (the eval harness and the
        editor read them through the API), ordered by beat_number; [] when the
        scene has none."""
        from app.core.orm_models import Chapter, Scene, SceneBeat

        chapter = Chapter(book_id=test_book.id, title="One", position=0)
        async_session.add(chapter)
        await async_session.flush()
        beaten, bare = (
            Scene(
                user_id=test_book.user_id,
                chapter_id=chapter.id,
                position=i,
                status="completed",
                content=text,
            )
            for i, text in enumerate(("  First. Second.\n", "Bare."))
        )
        async_session.add_all([beaten, bare])
        await async_session.flush()
        async_session.add_all(
            [
                SceneBeat(
                    scene_id=beaten.id, beat_number=1, description="d1", content="2."
                ),
                SceneBeat(scene_id=beaten.id, beat_number=0, content="1."),
            ]
        )
        await async_session.commit()

        url = f"/api/v1/scenes/{beaten.id}"
        body = (await client.get(url, headers=auth_headers)).json()
        assert body["content"] == "  First. Second.\n"  # the raw draft
        assert body["beats"] == [
            {"beat_number": 0, "description": None, "content": "1."},
            {"beat_number": 1, "description": "d1", "content": "2."},
        ]
        url = f"/api/v1/scenes/{bare.id}"
        assert (await client.get(url, headers=auth_headers)).json()["beats"] == []


class TestSceneTitles:
    """SPEC-37: a generated scene is named by `scene_batch.scene_title`, and the
    author renames it with an unversioned PATCH."""

    async def test_a_planned_scene_takes_its_beats_title(
        self, client, auth_headers, async_session, test_book
    ):
        from sqlalchemy import select

        from app.core.orm_models import Scene
        from app.jobs.scene_batch import scene_title
        from tests.unit.test_planned_beats import _PLANNED, _beat_book, _requests

        _, promoted, _ = await _beat_book(
            client, auth_headers, async_session, test_book
        )
        r = await client.post(
            _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
        )
        assert r.status_code == 200, r.text
        (scene,) = (await async_session.execute(select(Scene))).scalars().all()
        assert scene.title == "Intake"
        # The chained scenes are created from these requests by the same rule.
        requests = await _requests(async_session)
        assert [scene_title(q) for q in requests] == ["Intake", "Lunch", "Caseworker"]

    async def test_a_single_scene_takes_the_beat_title_else_six_words(
        self, client, auth_headers, async_session, test_book
    ):
        from uuid import UUID

        from app.core.orm_models import Scene
        from tests.unit.test_planned_beats import _beat_book

        ids, promoted, (_, b2, _) = await _beat_book(
            client, auth_headers, async_session, test_book
        )
        url = f"/api/v1/books/chapters/{promoted['chapter_id']}/scenes/generate"
        body = {
            "character_ids": [ids["Milo Voss"]],
            "scene_description": "Zara explains the afterlife over a long lunch",
            "setting": "",
            "emotional_tone": "",
        }
        titles = []
        for extra in ({"planned_beat_id": b2}, {}):
            r = await client.post(url, json={**body, **extra}, headers=auth_headers)
            assert r.status_code == 200, r.text
            scene = await async_session.get(Scene, UUID(r.json()["scene_id"]))
            titles.append(scene.title)
        assert titles == ["Lunch", "Zara explains the afterlife over a"]

    async def test_patch_renames_the_owners_scene_only(
        self, client, auth_headers, async_session, test_book
    ):
        from app.core.orm_models import Chapter, Scene, User
        from app.core.security import create_access_token, get_password_hash

        chapter = Chapter(book_id=test_book.id, title="One", position=0)
        async_session.add(chapter)
        await async_session.flush()
        scene = Scene(user_id=test_book.user_id, chapter_id=chapter.id, position=0)
        other = User(email="o@example.com", hashed_password=get_password_hash("x" * 12))
        async_session.add_all([scene, other])
        await async_session.commit()
        url = f"/api/v1/books/scenes/{scene.id}"

        r = await client.patch(url, json={"title": "  Retained "}, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"id": str(scene.id), "title": "Retained"}
        got = await client.get(f"/api/v1/scenes/{scene.id}", headers=auth_headers)
        assert got.json()["title"] == "Retained"

        for bad in ("  ", "x" * 501):
            r = await client.patch(url, json={"title": bad}, headers=auth_headers)
            assert r.status_code == 422, bad
        token = create_access_token(data={"sub": str(other.id)})
        r = await client.patch(
            url,
            json={"title": "Mine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Scene not found"
