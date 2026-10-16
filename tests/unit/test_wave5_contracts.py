"""Wave-5 day-0 contracts (tpm-plan-wave5 §1): the book's plan and the chapter's
earlier beats are read once beside `scene_chapter`, and a chunk's
`source_location` reads back as its paragraph."""

from datetime import datetime, timezone

import pytest

from app.jobs.scene_batch import scene_title
from app.orchestration.prose import book_plan, earlier_beats
from app.parsing.character_extractor import source_location, source_paragraphs
from tests.unit.factories import (
    add_chapter,
    add_planned_chapter,
    add_scene,
    add_scene_beats,
)

pytestmark = pytest.mark.unit


def _beats(*titles):
    return [{"title": t, "summary": f"{t} happens"} for t in titles]


async def test_book_plan_follows_chapter_order_and_leaves_out_the_trash(
    async_session, test_book
):
    first = await add_planned_chapter(async_session, test_book, _beats("A1", "A2"))
    second = await add_planned_chapter(async_session, test_book, _beats("B1"), 1)
    trashed = await add_planned_chapter(async_session, test_book, _beats("C1"), 2)
    trashed.deleted_at = datetime.now(timezone.utc)
    # The second moved before the first (through a free slot: positions are unique).
    for chapter, position in ((first, 9), (second, 0), (first, 1)):
        chapter.position = position
        await async_session.flush()

    plan = await book_plan(async_session, test_book.id)
    assert [(n, b["title"]) for n, b in plan] == [(1, "B1"), (2, "A1"), (2, "A2")]
    assert plan[0][1]["summary"] == "B1 happens"


async def test_earlier_beats_reads_the_chapter_s_live_written_scenes(
    async_session, test_book
):
    chapter = await add_chapter(async_session, test_book)
    one = await add_scene(async_session, test_book, chapter, 0)
    one.scene_request = {"beat_title": "Mid-Sentence", "scene_description": "x"}
    two = await add_scene(async_session, test_book, chapter, 1)
    two.deleted_at = datetime.now(timezone.utc)
    three = await add_scene(async_session, test_book, chapter, 2, status="processing")
    await add_scene_beats(async_session, one, ["Milo wakes.", "Prava smiles."])
    await add_scene_beats(async_session, two, ["Trashed."])

    assert await earlier_beats(async_session, three) == [
        (scene_title(one.scene_request), ["Milo wakes.", "Prava smiles."])
    ]
    assert await earlier_beats(async_session, one) == []
    assert await earlier_beats(async_session, None) == []


def test_source_paragraph_reads_the_extractor_s_locations():
    assert source_location("dialogue_9") == ("dialogue", 9)
    assert source_location("paragraph_13") == ("paragraph", 13)
    assert source_location("") is None
    assert source_location("manual") is None
    assert source_paragraphs("a\n\n b\n\n") == ["a", " b", ""]
