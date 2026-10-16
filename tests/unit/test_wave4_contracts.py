"""Wave-4 day-0 contracts (tpm-plan-wave4 §1(d)): `written_beats` maps each
written beat to its scene, and every chapter dict carries its `number`."""

import uuid

import pytest

from app.jobs import scene_batch
from tests.unit.factories import add_chapter, add_scene

pytestmark = pytest.mark.unit


async def test_written_beats_maps_beat_to_scene_and_leaves_out_failed(
    async_session, test_book
):
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    chapter = await add_chapter(async_session, test_book)
    live = await add_scene(async_session, test_book, chapter, 0, a)
    await add_scene(async_session, test_book, chapter, 1, b, status="failed")
    await add_scene(async_session, test_book, chapter, 2, c, status="cancelled")
    await add_scene(async_session, test_book, chapter, 3, None)  # a manual scene
    assert await scene_batch.written_beats(async_session, chapter.id) == {
        a: str(live.id)
    }


async def test_a_chapter_dict_carries_its_number(client, auth_headers, test_book):
    url = f"/api/v1/books/{test_book.id}/chapters"
    for title in ("One", "Two"):
        r = await client.post(url, json={"title": title}, headers=auth_headers)
        assert r.status_code == 201, r.text
    assert r.json()["position"] == 1 and r.json()["number"] == 2
    r = await client.get(f"/api/v1/books/{test_book.id}", headers=auth_headers)
    assert [c["number"] for c in r.json()["chapters"]] == [1, 2]
