"""SPEC-04 — scenes are book-rooted.

Every scene carries a non-null book_id; deleting a book takes its scenes (and
their beats and revisions) with it; deleting a chapter never silently orphans
prose — the author moves its scenes or deletes them, and a bare delete is a 409.
"""

from datetime import datetime, timedelta, timezone
import re
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from app.core.orm_models import (
    Chapter,
    EntityVersion,
    Scene,
    SceneBeat,
    SceneRevision,
)
from tests.unit.factories import add_chapter, add_scene

pytestmark = pytest.mark.unit


async def _book(client, headers, title="B"):
    r = await client.post("/api/v1/books/", json={"title": title}, headers=headers)
    return r.json()["id"]


async def _chapter(client, headers, book_id, title):
    r = await client.post(
        f"/api/v1/books/{book_id}/chapters", json={"title": title}, headers=headers
    )
    return r.json()["id"]


async def _scene(client, headers, chapter_id, content):
    r = await client.post(
        f"/api/v1/books/chapters/{chapter_id}/scenes",
        json={"title": content, "content": content},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["scene_id"]


async def _chapter_scenes(client, headers, chapter_id):
    r = await client.get(f"/api/v1/books/chapters/{chapter_id}", headers=headers)
    return [(s["id"], s["position"]) for s in r.json()["scenes"]]


async def _count(session, model, **where):
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return (await session.execute(q)).scalar_one()


class TestSceneCarriesItsBook:
    async def test_api_created_scene_has_the_chapters_book(
        self, client, auth_headers, async_session
    ):
        book_id = await _book(client, auth_headers)
        ch = await _chapter(client, auth_headers, book_id, "One")
        sid = await _scene(client, auth_headers, ch, "prose")
        scene = await async_session.get(Scene, UUID(sid))
        assert scene.book_id == UUID(book_id)

    async def test_orm_insert_derives_book_from_chapter_then_source(
        self, async_session, test_user, test_book, test_source
    ):
        chapter = Chapter(book_id=test_book.id, title="C", position=0)
        async_session.add(chapter)
        await async_session.flush()
        in_chapter = Scene(user_id=test_user.id, chapter_id=chapter.id, position=0)
        from_source = Scene(user_id=test_user.id, source_id=test_source.id)
        async_session.add_all([in_chapter, from_source])
        await async_session.commit()
        assert in_chapter.book_id == test_book.id
        assert from_source.book_id == test_book.id

    async def test_a_scene_with_no_book_is_rejected(self, async_session, test_user):
        from sqlalchemy.exc import IntegrityError

        async_session.add(Scene(user_id=test_user.id, position=0))
        with pytest.raises(IntegrityError):
            await async_session.commit()
        await async_session.rollback()


class TestBookDeleteCascades:
    async def test_deleting_a_book_deletes_its_scenes_beats_and_revisions(
        self, client, auth_headers, async_session
    ):
        # The cascade is the database's (ON DELETE CASCADE, passive_deletes on
        # Book.scenes), so enforce FKs on this sqlite connection as Postgres does.
        await async_session.execute(text("PRAGMA foreign_keys=ON"))
        book_id = await _book(client, auth_headers)
        keep_id = await _book(client, auth_headers, "Keep")
        ch = await _chapter(client, auth_headers, book_id, "One")
        sid = await _scene(client, auth_headers, ch, "first draft")
        keep_sid = await _scene(
            client,
            auth_headers,
            await _chapter(client, auth_headers, keep_id, "K"),
            "kept",
        )
        r = await client.put(
            f"/api/v1/books/scenes/{sid}/content",
            json={"content": "second draft"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        async_session.add(SceneBeat(scene_id=UUID(sid), beat_number=1, description="b"))
        await async_session.commit()
        assert await _count(async_session, SceneRevision, scene_id=UUID(sid)) == 1

        r = await client.delete(f"/api/v1/books/{book_id}", headers=auth_headers)
        assert r.status_code == 204
        async_session.expunge_all()
        assert await _count(async_session, Scene, id=UUID(sid)) == 0
        assert await _count(async_session, SceneBeat, scene_id=UUID(sid)) == 0
        assert await _count(async_session, SceneRevision, scene_id=UUID(sid)) == 0
        # another book's scene is untouched
        assert await _count(async_session, Scene, id=UUID(keep_sid)) == 1


class TestChapterDeleteAsks:
    @pytest.fixture
    async def tree(self, client, auth_headers):
        book_id = await _book(client, auth_headers)
        a = await _chapter(client, auth_headers, book_id, "A")
        b = await _chapter(client, auth_headers, book_id, "B")
        a_scenes = [await _scene(client, auth_headers, a, f"a{i}") for i in range(3)]
        b_scenes = [await _scene(client, auth_headers, b, f"b{i}") for i in range(2)]
        return {"book": book_id, "a": a, "b": b, "a_s": a_scenes, "b_s": b_scenes}

    async def test_bare_delete_with_scenes_is_409_and_changes_nothing(
        self, client, auth_headers, tree
    ):
        r = await client.delete(
            f"/api/v1/books/chapters/{tree['a']}", headers=auth_headers
        )
        assert r.status_code == 409
        assert r.json()["detail"]["scene_count"] == 3
        assert [
            s for s, _ in await _chapter_scenes(client, auth_headers, tree["a"])
        ] == (tree["a_s"])

    async def test_move_appends_in_order_with_contiguous_positions(
        self, client, auth_headers, tree
    ):
        r = await client.delete(
            f"/api/v1/books/chapters/{tree['a']}?scenes=move&to={tree['b']}",
            headers=auth_headers,
        )
        assert r.status_code == 204, r.text
        landed = await _chapter_scenes(client, auth_headers, tree["b"])
        assert [s for s, _ in landed] == tree["b_s"] + tree["a_s"]
        assert [p for _, p in landed] == list(range(5))
        r = await client.get(
            f"/api/v1/books/chapters/{tree['a']}", headers=auth_headers
        )
        assert r.status_code == 404

    async def test_move_needs_a_target_in_the_same_book(
        self, client, auth_headers, tree
    ):
        other = await _chapter(
            client, auth_headers, await _book(client, auth_headers, "Other"), "X"
        )
        for query in (
            "scenes=move",
            f"scenes=move&to={tree['a']}",
            f"scenes=move&to={other}",
        ):
            r = await client.delete(
                f"/api/v1/books/chapters/{tree['a']}?{query}", headers=auth_headers
            )
            assert r.status_code == 400, query
        r = await client.delete(
            f"/api/v1/books/chapters/{tree['a']}?scenes=archive", headers=auth_headers
        )
        assert r.status_code == 422
        assert len(await _chapter_scenes(client, auth_headers, tree["a"])) == 3

    async def test_delete_trashes_scenes_and_keeps_their_prose_as_versions(
        self, client, auth_headers, async_session, tree
    ):
        r = await client.delete(
            f"/api/v1/books/chapters/{tree['a']}?scenes=delete", headers=auth_headers
        )
        assert r.status_code == 204, r.text
        async_session.expunge_all()
        for sid in tree["a_s"]:  # trashed with the chapter (SPEC-13)
            assert (await async_session.get(Scene, UUID(sid))).deleted_at is not None
        kept = (
            (
                await async_session.execute(
                    select(EntityVersion).where(EntityVersion.entity_type == "scene")
                )
            )
            .scalars()
            .all()
        )
        assert {str(v.entity_id) for v in kept} == set(tree["a_s"])
        assert {v.reason for v in kept} == {"deleted"}
        assert {v.content["content"] for v in kept} == {"a0", "a1", "a2"}
        # the other chapter is untouched
        assert len(await _chapter_scenes(client, auth_headers, tree["b"])) == 2

    async def test_an_empty_chapter_deletes_without_asking(
        self, client, auth_headers, tree
    ):
        empty = await _chapter(client, auth_headers, tree["book"], "Empty")
        r = await client.delete(f"/api/v1/books/chapters/{empty}", headers=auth_headers)
        assert r.status_code == 204


class TestUserLevelSceneRoutes:
    async def test_list_requires_auth(self, client):
        assert (await client.get("/api/v1/scenes/")).status_code == 401

    async def test_list_and_get_reach_scenes_through_the_book(
        self, client, auth_headers, async_session, test_user
    ):
        book_id = await _book(client, auth_headers)
        sid = await _scene(
            client,
            auth_headers,
            await _chapter(client, auth_headers, book_id, "C"),
            "prose",
        )
        listed = (await client.get("/api/v1/scenes/", headers=auth_headers)).json()
        assert [s["id"] for s in listed["scenes"]] == [sid]
        assert listed["total"] == 1
        assert listed["scenes"][0]["book_id"] == book_id

        got = await client.get(f"/api/v1/scenes/{sid}", headers=auth_headers)
        assert got.status_code == 200
        assert got.json()["book_id"] == book_id

        r = await client.delete(f"/api/v1/scenes/{sid}", headers=auth_headers)
        assert r.status_code == 204
        assert (
            await client.get(f"/api/v1/scenes/{sid}", headers=auth_headers)
        ).status_code == 404

    async def test_another_tenants_scene_is_404(
        self, client, auth_headers, async_session
    ):
        from app.core.security import create_access_token, get_password_hash
        from app.core.orm_models import User

        book_id = await _book(client, auth_headers)
        sid = await _scene(
            client,
            auth_headers,
            await _chapter(client, auth_headers, book_id, "C"),
            "prose",
        )
        other = User(email="o@example.com", hashed_password=get_password_hash("x" * 12))
        async_session.add(other)
        await async_session.commit()
        headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
        }
        assert (
            await client.get(f"/api/v1/scenes/{sid}", headers=headers)
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/scenes/{sid}", headers=headers)
        ).status_code == 404
        listed = (await client.get("/api/v1/scenes/", headers=headers)).json()
        assert listed["scenes"] == [] and listed["total"] == 0


class TestBookOrder:
    async def test_get_scene_knows_its_chapter_and_neighbours_in_book_order(
        self, client, auth_headers, async_session, test_book
    ):
        """SPEC-37: neighbours follow chapter position, then scene position,
        skipping trashed scenes; an unfiled scene has no chapter and no
        neighbours."""
        book_id = str(test_book.id)
        a = await _chapter(client, auth_headers, book_id, "A")
        b = await _chapter(client, auth_headers, book_id, "B")
        a0, a1 = [await _scene(client, auth_headers, a, f"a{i}") for i in range(2)]
        b0, b1 = [await _scene(client, auth_headers, b, f"b{i}") for i in range(2)]
        r = await client.patch(
            f"/api/v1/books/chapters/{b}/position",
            json={"position": 0},
            headers=auth_headers,
        )
        assert r.status_code == 200
        r = await client.delete(f"/api/v1/scenes/{b1}", headers=auth_headers)
        assert r.status_code == 204
        then = datetime(2026, 1, 1, tzinfo=timezone.utc)
        loose = await add_scene(async_session, test_book, created_at=then)
        # A newer unfiled scene with a higher leftover position still lists first.
        newer = await add_scene(
            async_session,
            test_book,
            position=5,
            created_at=then + timedelta(minutes=1),
        )
        await async_session.commit()

        async def where(sid):
            got = await client.get(f"/api/v1/scenes/{sid}", headers=auth_headers)
            body = got.json()
            return body["chapter"], body["neighbours"]

        assert await where(b0) == (
            {"id": b, "number": 1, "title": "B"},
            {"prev": None, "next": a0},
        )
        assert (await where(a0))[1] == {"prev": b0, "next": a1}
        assert await where(a1) == (
            {"id": a, "number": 2, "title": "A"},
            {"prev": a0, "next": None},
        )
        assert await where(loose.id) == (None, {"prev": None, "next": None})
        url = f"/api/v1/books/{book_id}/scenes"
        listed = (await client.get(url, headers=auth_headers)).json()["scenes"]
        assert [s["id"] for s in listed] == [b0, a0, a1, str(newer.id), str(loose.id)]


async def test_a_scene_in_a_trashed_chapter_reads_as_unfiled(
    client, auth_headers, async_session, test_book
):
    chapter = await add_chapter(async_session, test_book)
    scene = await add_scene(async_session, test_book, chapter)
    chapter.deleted_at = datetime.now(timezone.utc)
    await async_session.commit()
    body = (await client.get(f"/api/v1/scenes/{scene.id}", headers=auth_headers)).json()
    assert (body["chapter"], body["neighbours"]) == (None, {"prev": None, "next": None})


def test_no_query_lists_scenes_by_user_id_alone():
    """Acceptance 4: every `Scene.user_id ==` filter in app/ sits beside the
    book ownership check — the user id is the tenant guard, never the scope."""
    app_dir = Path(__file__).resolve().parents[2] / "app"
    pattern = re.compile(r"Scene(ORM)?\.user_id\s*==")
    hits = []
    for path in app_dir.rglob("*.py"):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if pattern.search(line):
                window = "\n".join(lines[max(0, i - 10) : i + 10])
                hits.append((path.name, i + 1, "BookORM.user_id" in window))
    assert hits, "expected the tenant guard to be present"
    assert all(ok for *_, ok in hits), hits
