"""SPEC-23: planned beats are data on the chapter.

Promote writes `chapters.planned_beats` from the outline node's children (their
node_ids, cast, setting, tone and secondary names); generate-planned writes
each scene from its own beat; the chapter PATCH edits the beats strictly and
versions them; a chapter from before the migration still generates from its
summary text; GET /plans and promote expose `suggested_characters`.
"""

import uuid
from collections import Counter

import pytest
from sqlalchemy import select

from app.core.orm_models import BookPlan, Chapter, Character, Job, Scene
from app.jobs import scene_batch
from app.planning import secondaries
from app.planning.planned_beat import beat_scene_description
from tests.unit.test_outline_handoff import _cast

pytestmark = pytest.mark.unit

_PLANNED = "/api/v1/books/chapters/{}/scenes/generate-planned"


def _ids(n):
    return [str(uuid.uuid4()) for _ in range(n)]


async def _outline(session, book, nodes):
    """Store an outline directly: until SPEC-24's validator keeps the beat
    fields, a PUT would drop them, and promote must map them either way."""
    session.add(BookPlan(book_id=book.id, kind="outline", content=nodes))
    await session.commit()


async def _beat_book(client, auth_headers, async_session, test_book):
    """Chapter 2 'Milo Voss' (POV Milo, cast Milo+Zara) with three beats: one
    with its own cast (Edric) and tone, one with no cast, one naming Prava (a
    secondary not in the cast)."""
    ids = await _cast(async_session, test_book)
    node_id, b1, b2, b3 = _ids(4)
    nodes = [
        {
            "node_id": node_id,
            "title": "Chapter 2: Milo Voss",
            "summary": "Milo is processed.",
            "pov": "Milo Voss",
            "character_ids": [ids["Milo Voss"], ids["Zara Okafor"]],
            "children": [
                {
                    "node_id": b1,
                    "title": "Intake",
                    "summary": "Edric reviews Milo's file",
                    "character_ids": [ids["Edric Thane"]],
                    "setting": "Intake Atrium, Aeon Holdings",
                    "tone": "bureaucratic dread",
                    "children": [],
                },
                {
                    "node_id": b2,
                    "title": "Lunch",
                    "summary": "Zara explains the afterlife",
                    "children": [],
                },
                {
                    "node_id": b3,
                    "title": "Caseworker",
                    "summary": "Prava stamps the form",
                    "character_ids": [],
                    "secondary_names": ["Prava"],
                    "children": [],
                },
            ],
        }
    ]
    await _outline(async_session, test_book, nodes)
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_id": node_id},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return ids, r.json(), (b1, b2, b3)


async def _requests(async_session):
    """The scene requests of the (single) batch in flight, in order."""
    (job,) = (await async_session.execute(select(Job))).scalars().all()
    return [job.payload["request"], *job.payload["batch"]["remaining"]]


# --- promote ---------------------------------------------------------------------


async def test_promote_writes_beats_as_data(
    client, auth_headers, async_session, test_book
):
    ids, promoted, (b1, b2, b3) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    assert promoted["title"] == "Milo Voss"  # "Chapter 2: " stripped
    chapter = await async_session.get(Chapter, uuid.UUID(promoted["chapter_id"]))
    assert chapter.title == "Milo Voss"
    assert chapter.summary == "Milo is processed."
    assert "Planned beats:" not in chapter.summary
    beats = chapter.planned_beats
    assert [b["node_id"] for b in beats] == [b1, b2, b3]
    assert beats[0]["character_ids"] == [ids["Edric Thane"]]
    assert beats[0]["setting"] == "Intake Atrium, Aeon Holdings"
    assert beats[0]["tone"] == "bureaucratic dread"
    assert beats[1]["character_ids"] == [] and beats[1]["tone"] == ""
    assert beats[2]["secondary_names"] == ["Prava"]

    detail = (
        await client.get(
            f"/api/v1/books/chapters/{promoted['chapter_id']}", headers=auth_headers
        )
    ).json()
    assert [b["title"] for b in detail["planned_beats"]] == [
        "Intake",
        "Lunch",
        "Caseworker",
    ]


async def test_promote_moves_a_secondary_that_now_resolves_into_the_cast(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    prava = Character(
        book_id=test_book.id, user_id=test_book.user_id, name="Prava", role="minor"
    )
    async_session.add(prava)
    await async_session.commit()
    gone = str(uuid.uuid4())  # a character no longer in the book
    node_id, beat_id = _ids(2)
    await _outline(
        async_session,
        test_book,
        [
            {
                "node_id": node_id,
                "title": "Intake",
                "summary": "s",
                "children": [
                    {
                        "node_id": beat_id,
                        "title": "Stamp",
                        "summary": "the form",
                        "character_ids": [ids["Milo Voss"], gone],
                        "secondary_names": ["Prava", "Old Fen"],
                    }
                ],
            }
        ],
    )
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_index": 0},
        headers=auth_headers,
    )
    chapter = await async_session.get(Chapter, uuid.UUID(r.json()["chapter_id"]))
    (beat,) = chapter.planned_beats
    assert beat["character_ids"] == [ids["Milo Voss"], str(prava.id)]  # moved
    assert beat["secondary_names"] == ["Old Fen"]  # not duplicated


async def test_an_all_number_title_falls_back_to_the_position(
    client, auth_headers, async_session, test_book
):
    await _outline(async_session, test_book, [{"title": "Chapter 7:", "summary": ""}])
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote",
        json={"node_index": 0},
        headers=auth_headers,
    )
    assert r.json()["title"] == "Chapter 1"


# --- generate-planned ---------------------------------------------------------------


async def test_each_planned_scene_takes_its_beats_cast_setting_and_tone(
    client, auth_headers, async_session, test_book
):
    ids, promoted, (b1, b2, b3) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    first, second, third = await _requests(async_session)
    milo, zara, edric = ids["Milo Voss"], ids["Zara Okafor"], ids["Edric Thane"]

    # The beat's own cast plus the chapter POV character.
    assert first["character_ids"] == [milo, edric]
    assert first["characters"] == ["Milo Voss", "Edric Thane"]
    assert first["setting"] == "Intake Atrium, Aeon Holdings"
    assert first["emotional_tone"] == "bureaucratic dread"
    assert first["pov_character"] == "Milo Voss"
    assert first["planned_beat_id"] == b1
    assert first["secondary_names"] == []
    assert first["scene_description"] == "Intake: Edric reviews Milo's file"

    # No beat cast: the chapter's. No tone: "" (never "neutral").
    assert second["character_ids"] == [milo, zara]
    assert second["emotional_tone"] == "" and second["setting"] == ""
    assert second["planned_beat_id"] == b2

    # Prava is not in the cast: passed on as a secondary name.
    assert third["character_ids"] == [milo, zara]
    assert third["secondary_names"] == ["Prava"]
    assert third["planned_beat_id"] == b3


async def test_an_explicit_cast_overrides_every_beat(
    client, auth_headers, async_session, test_book
):
    ids, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    zara = ids["Zara Okafor"]
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]),
        json={
            "character_ids": [zara],
            "setting": "the office",
            "emotional_tone": "wry",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    for request in await _requests(async_session):
        assert request["character_ids"] == [ids["Milo Voss"], zara]  # + POV
        assert request["setting"] == "the office"
        assert request["emotional_tone"] == "wry"


async def test_a_secondary_added_to_the_cast_since_promote_is_cast(
    client, auth_headers, async_session, test_book
):
    ids, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    prava = Character(
        book_id=test_book.id, user_id=test_book.user_id, name="Prava", role="minor"
    )
    async_session.add(prava)
    await async_session.commit()
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    third = (await _requests(async_session))[2]
    # Prava resolves now, so she is the beat's cast (not the chapter's).
    assert third["character_ids"] == [ids["Milo Voss"], str(prava.id)]
    assert third["secondary_names"] == []


async def test_the_preflight_sums_each_beats_cast(
    client, auth_headers, async_session, test_book, monkeypatch
):
    from app.core import estimates

    _, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    seen = {}
    real = estimates.scene

    def spy(**kw):
        seen.setdefault("sizes", []).append(kw["cast_size"])
        return real(**kw)

    async def refuse(db, user_id, est):
        seen["calls"] = est.calls
        return {"allowed": False, "reason": "no"}

    monkeypatch.setattr(estimates, "scene", spy)
    monkeypatch.setattr(estimates, "assess", refuse)
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
    )
    assert r.status_code == 429
    assert seen["sizes"] == [2, 2, 2]  # Milo+Edric, Milo+Zara, Milo+Zara
    assert (
        seen["calls"]
        == 3
        * real(canon_chars=0, cast_context_chars=0, target_words=800, cast_size=2).calls
    )


async def test_editing_the_summary_does_not_change_the_requests(
    client, auth_headers, async_session, test_book
):
    _, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    chapter_id = promoted["chapter_id"]
    r = await client.post(_PLANNED.format(chapter_id), json={}, headers=auth_headers)
    before = await _requests(async_session)
    await async_session.execute(Job.__table__.delete())
    await async_session.execute(Scene.__table__.delete())
    await async_session.commit()

    r = await client.patch(
        f"/api/v1/books/chapters/{chapter_id}",
        json={"summary": "Rewritten.\n\nPlanned beats:\n- Nothing: at all"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    r = await client.post(_PLANNED.format(chapter_id), json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert await _requests(async_session) == before


# --- the chapter PATCH --------------------------------------------------------------


async def test_patch_planned_beats_is_strict_and_versioned(
    client, auth_headers, async_session, test_book
):
    ids, promoted, (b1, _, _) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    chapter_id = promoted["chapter_id"]
    url = f"/api/v1/books/chapters/{chapter_id}"
    beat = {
        "node_id": b1,
        "title": "Intake",
        "summary": "Zara files the form",
        "character_ids": [str(uuid.uuid4())],  # not this book's
    }
    r = await client.patch(url, json={"planned_beats": [beat]}, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["detail"]["unknown"] == beat["character_ids"]

    beat["character_ids"] = [ids["Zara Okafor"]]
    beat["tone"] = "  wry "
    r = await client.patch(url, json={"planned_beats": [beat]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    (stored,) = r.json()["planned_beats"]
    assert stored["node_id"] == b1 and stored["tone"] == "wry"

    versions = (
        await client.get(
            f"/api/v1/books/{test_book.id}/versions/chapter/{chapter_id}",
            headers=auth_headers,
        )
    ).json()
    assert len(versions["versions"]) == 2  # created + edited
    # Restoring v1 brings back the three promoted beats.
    r = await client.post(
        f"/api/v1/books/{test_book.id}/versions/chapter/{chapter_id}/restore/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert [b["title"] for b in chapter.planned_beats] == [
        "Intake",
        "Lunch",
        "Caseworker",
    ]


async def test_restoring_beats_drops_characters_no_longer_live(
    client, auth_headers, async_session, test_book
):
    ids, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    chapter_id = promoted["chapter_id"]
    await client.patch(
        f"/api/v1/books/chapters/{chapter_id}",
        json={"title": "Renamed"},
        headers=auth_headers,
    )
    from datetime import datetime, timezone

    edric = await async_session.get(Character, uuid.UUID(ids["Edric Thane"]))
    edric.deleted_at = datetime.now(timezone.utc)  # trashed
    await async_session.commit()
    r = await client.post(
        f"/api/v1/books/{test_book.id}/versions/chapter/{chapter_id}/restore/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    chapter = await async_session.get(Chapter, uuid.UUID(chapter_id))
    assert chapter.title == "Milo Voss"
    assert chapter.planned_beats[0]["character_ids"] == []


# --- chapters from before the migration -----------------------------------------------


async def test_a_summary_only_chapter_still_generates(
    client, auth_headers, async_session, test_book
):
    ids = await _cast(async_session, test_book)
    chapter = Chapter(
        book_id=test_book.id,
        title="Old",
        position=0,
        summary="Opening.\n\nPlanned beats:\n- Storm: the moor floods\n- Lunch:",
        character_ids=[ids["Milo Voss"]],
    )
    async_session.add(chapter)
    await async_session.commit()
    beats = scene_batch.planned_beats(chapter)
    assert [beat_scene_description(b) for b in beats] == [
        "Storm: the moor floods",
        "Lunch:",
    ]
    # A scene written under the old format still counts as written.
    async_session.add(
        Scene(
            user_id=test_book.user_id,
            book_id=test_book.id,
            chapter_id=chapter.id,
            position=0,
            scene_description="Storm: the moor floods",
            status="completed",
        )
    )
    await async_session.commit()
    r = await client.post(_PLANNED.format(chapter.id), json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (r.json()["total"], r.json()["queued"], r.json()["skipped"]) == (2, 1, 1)
    (request,) = await _requests(async_session)
    assert request["scene_description"] == "Lunch:"
    assert request["character_ids"] == [ids["Milo Voss"]]
    assert request["emotional_tone"] == ""


def test_legacy_lines_parse_into_beats_that_rebuild_the_line():
    chapter = type(
        "C",
        (),
        {
            "planned_beats": None,
            "summary": "s\n\nPlanned beats:\n- Part 1: Intake: forms\n- Bare\n- A: b",
        },
    )()
    beats = scene_batch.planned_beats(chapter)
    assert [beat_scene_description(b) for b in beats] == [
        "Part 1: Intake: forms",
        "Bare:",
        "A: b",
    ]
    # "Bare" (a hand-written line with no colon) matches its old scene too.
    written = Counter({"Bare": 1, "A: b": 1})
    assert scene_batch.unwritten_beats(beats, written) == [beats[0]]
    assert scene_batch.summary_without_beats(chapter.summary) == "s"


def test_data_beats_win_over_summary_text():
    chapter = type(
        "C",
        (),
        {
            "planned_beats": [{"title": "Data", "summary": "beat"}],
            "summary": "s\n\nPlanned beats:\n- Text: beat",
        },
    )()
    assert [b["title"] for b in scene_batch.planned_beats(chapter)] == ["Data"]


# --- suggested_characters (§1.2) ------------------------------------------------------


_PRAVA = {"name": "Prava", "synopsis_sentence": "Prava, the intake caseworker."}


async def test_plans_and_promote_return_suggested_characters(
    client, auth_headers, async_session, test_book, monkeypatch
):
    seen = []

    def fake(names, canon):
        seen.append((list(names), canon is not None))
        return [_PRAVA]

    monkeypatch.setattr(secondaries, "suggested_characters", fake)
    await _cast(async_session, test_book)
    await _outline(
        async_session,
        test_book,
        [
            {"title": "One", "summary": "a", "secondary_names": ["One"]},
            {"title": "Two", "summary": "b", "secondary_names": ["Two"]},
        ],
    )
    base = f"/api/v1/books/{test_book.id}/plans"

    plans = (await client.get(base, headers=auth_headers)).json()["plans"]
    assert plans[0]["suggested_characters"] == [_PRAVA]

    one = (
        await client.post(
            f"{base}/promote", json={"node_index": 1}, headers=auth_headers
        )
    ).json()
    assert one["suggested_characters"] == [_PRAVA]
    every = (await client.post(f"{base}/promote-all", headers=auth_headers)).json()
    assert every["suggested_characters"] == [_PRAVA]
    assert [titles for titles, _ in seen] == [
        ["One", "Two"],  # GET: the whole outline
        ["Two"],  # promote: that chapter's node
        ["One", "Two"],  # promote-all
    ]


async def test_suggestions_only_for_a_ready_outline_and_never_fatal(
    client, auth_headers, async_session, test_book, monkeypatch
):
    calls = []

    def boom(nodes, canon):
        calls.append(1)
        raise RuntimeError("bug")

    monkeypatch.setattr(secondaries, "suggested_characters", boom)
    async_session.add_all(
        [
            BookPlan(
                book_id=test_book.id,
                kind="outline",
                content=[{"title": "x"}],
                status="generating",
            ),
            BookPlan(book_id=test_book.id, kind="beat_sheet", content=[]),
        ]
    )
    await async_session.commit()
    plans = (
        await client.get(f"/api/v1/books/{test_book.id}/plans", headers=auth_headers)
    ).json()["plans"]
    assert [p["suggested_characters"] for p in plans] == [[], []]
    assert calls == []  # neither plan qualifies: no canon build, no call

    plan = (
        await async_session.execute(select(BookPlan).where(BookPlan.kind == "outline"))
    ).scalar_one()
    plan.status = "ready"
    await async_session.commit()
    plans = (
        await client.get(f"/api/v1/books/{test_book.id}/plans", headers=auth_headers)
    ).json()["plans"]
    assert all(p["suggested_characters"] == [] for p in plans)  # error -> []
    assert calls == [1]
