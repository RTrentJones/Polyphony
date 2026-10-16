"""Wave-4 day-0 contracts (tpm-plan-wave4 §1(d)): `written_beats` maps each
written beat to its scene, and every chapter dict carries its `number`."""

import uuid

import pytest

from app.core.orm_models import Chapter, Scene
from app.jobs import scene_batch

pytestmark = pytest.mark.unit


async def _chapter(async_session, book) -> Chapter:
    chapter = Chapter(book_id=book.id, position=0, title="Intake")
    async_session.add(chapter)
    await async_session.flush()
    return chapter


async def _scene(async_session, book, chapter, position, beat, status="completed"):
    scene = Scene(
        user_id=book.user_id,
        book_id=book.id,
        chapter_id=chapter.id,
        position=position,
        status=status,
        scene_request={"planned_beat_id": beat},
    )
    async_session.add(scene)
    await async_session.flush()
    return scene


async def test_written_beats_maps_beat_to_scene_and_leaves_out_failed(
    async_session, test_book
):
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    chapter = await _chapter(async_session, test_book)
    live = await _scene(async_session, test_book, chapter, 0, a)
    await _scene(async_session, test_book, chapter, 1, b, status="failed")
    await _scene(async_session, test_book, chapter, 2, c, status="cancelled")
    await _scene(async_session, test_book, chapter, 3, None)  # a manual scene
    assert await scene_batch.written_beats(async_session, chapter.id) == {
        a: str(live.id)
    }


async def test_two_live_scenes_of_one_beat_link_the_lower_position(
    async_session, test_book
):
    """S7: the single path can write a beat twice; "written" links the first
    scene in chapter order, whatever order the rows were inserted in."""
    beat = str(uuid.uuid4())
    chapter = await _chapter(async_session, test_book)
    await _scene(async_session, test_book, chapter, 1, beat)
    first = await _scene(async_session, test_book, chapter, 0, beat)
    assert await scene_batch.written_beats(async_session, chapter.id) == {
        beat: str(first.id)
    }


async def test_a_chapter_dict_carries_its_number(client, auth_headers, test_book):
    url = f"/api/v1/books/{test_book.id}/chapters"
    for title in ("One", "Two"):
        r = await client.post(url, json={"title": title}, headers=auth_headers)
        assert r.status_code == 201, r.text
    assert r.json()["position"] == 1 and r.json()["number"] == 2
    r = await client.get(f"/api/v1/books/{test_book.id}", headers=auth_headers)
    assert [c["number"] for c in r.json()["chapters"]] == [1, 2]
