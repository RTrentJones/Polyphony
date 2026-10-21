"""SPEC-23: planned beats are data on the chapter.

Promote writes `chapters.planned_beats` from the outline node's children (their
node_ids, cast, setting, tone and secondary names); generate-planned writes
each scene from its own beat; the chapter PATCH edits the beats strictly and
versions them; a scene counts as written by its beat's id only; restoring a
chapter version from before SPEC-23 converts its "Planned beats:" block once;
GET /plans and promote expose `suggested_characters`.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.orm_models import BookPlan, Chapter, Character, Job, Scene
from app.planning import secondaries
from tests.unit.factories import add_scene
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


async def test_a_chosen_model_rides_every_scene_of_the_batch(
    client, auth_headers, async_session, test_book
):
    """U1 (F): flash is on every request of the batch, not only the first."""
    _, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    body = {"model": "gemini-3.6-flash"}
    url = _PLANNED.format(promoted["chapter_id"])
    assert (await client.post(url, json=body, headers=auth_headers)).is_success
    assert [q["model"] for q in await _requests(async_session)] == [body["model"]] * 3
    (job,) = (await async_session.execute(select(Job))).scalars().all()
    assert job.payload["model"] == body["model"]  # the chain copies it per job


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

    async def refuse(db, user_id, est, model=None):
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


# --- beat identity, legacy text, restore ------------------------------------------------


async def test_a_renamed_beat_keeps_its_written_scene(
    client, auth_headers, async_session, test_book
):
    """Review B3: a scene is linked to its beat by id, so renaming the beat
    after its scene was written never writes a second one."""
    _, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    url = f"/api/v1/books/chapters/{promoted['chapter_id']}"
    beat = (await client.get(url, headers=auth_headers)).json()["planned_beats"][0]
    r = await client.patch(url, json={"planned_beats": [beat]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
    )
    assert (r.json()["total"], r.json()["queued"]) == (1, 1)
    await async_session.execute(Job.__table__.delete())  # the batch is done
    await async_session.commit()

    renamed = {**beat, "title": "Renamed", "summary": "something else"}
    r = await client.patch(url, json={"planned_beats": [renamed]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]), json={}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "nothing_to_do"
    assert (r.json()["total"], r.json()["skipped"]) == (1, 1)


async def test_a_typed_block_in_the_summary_plans_nothing(
    client, auth_headers, async_session, test_book
):
    """No runtime fallback (SPEC-23): a chapter's beats are its stored
    `planned_beats`; "Planned beats:" lines typed into a summary are text."""
    ids = await _cast(async_session, test_book)
    chapter = Chapter(
        book_id=test_book.id,
        title="Hand-made",
        position=0,
        summary="Opening.\n\nPlanned beats:\n- Storm: the moor floods\n- Lunch:",
        character_ids=[ids["Milo Voss"]],
    )
    async_session.add(chapter)
    await async_session.commit()
    r = await client.post(_PLANNED.format(chapter.id), json={}, headers=auth_headers)
    assert r.status_code == 422
    assert "no planned beats" in r.json()["detail"]
    detail = (
        await client.get(f"/api/v1/books/chapters/{chapter.id}", headers=auth_headers)
    ).json()
    assert detail["planned_beats"] == []


async def test_restoring_a_pre_spec23_version_converts_its_block_once(
    client, auth_headers, async_session, test_book
):
    from app.versioning import repository as versions_repo

    chapter = Chapter(book_id=test_book.id, title="Old", position=0, summary="Now.")
    async_session.add(chapter)
    await async_session.flush()
    # A version taken before SPEC-23: no `planned_beats` key, beats in the text.
    await versions_repo.snapshot(
        async_session,
        book_id=test_book.id,
        entity_type="chapter",
        entity_id=chapter.id,
        content={
            "title": "Old",
            "summary": "Then.\n\nPlanned beats:\n- Storm: the moor\n  floods\n- Lunch:",
            "status": "drafting",
        },
        reason="created",
    )
    await async_session.commit()
    chapter_id = chapter.id
    restore = f"/api/v1/books/{test_book.id}/versions/chapter/{chapter_id}/restore/1"
    old = "Then.\n\nPlanned beats:\n- Storm: the moor\n  floods\n- Lunch:"
    seen = []
    # The first restore converts the block; the second meets live beats, so
    # the block stays as text in the summary and the beats are untouched.
    for summary in ("Then.", old):
        r = await client.post(restore, headers=auth_headers)
        assert r.status_code == 200, r.text
        async_session.expire_all()
        live = await async_session.get(Chapter, chapter_id)
        assert live.summary == summary
        assert [(b["title"], b["summary"]) for b in live.planned_beats] == [
            ("Storm", "the moor floods"),
            ("Lunch", ""),
        ]
        seen.append([b["node_id"] for b in live.planned_beats])
    assert seen[0] == seen[1]


@pytest.mark.parametrize(
    "block",
    [
        "\n".join(f"- B{i}: s{i}" for i in range(13)),  # past the 12-beat cap
        "- A: a\n- : the author's orphan line",  # a line with no title
    ],
)
async def test_restoring_a_block_that_would_lose_text_keeps_it_as_text(
    client, auth_headers, async_session, test_book, block
):
    """NB-2: converting would drop a line, so the summary keeps every line."""
    from app.versioning import repository as versions_repo

    chapter = Chapter(book_id=test_book.id, title="Old", position=0, summary="Now.")
    async_session.add(chapter)
    await async_session.flush()
    chapter_id = chapter.id
    summary = f"Then.\n\nPlanned beats:\n{block}"
    await versions_repo.snapshot(
        async_session,
        book_id=test_book.id,
        entity_type="chapter",
        entity_id=chapter_id,
        content={"title": "Old", "summary": summary, "status": "drafting"},
        reason="created",
    )
    await async_session.commit()
    r = await client.post(
        f"/api/v1/books/{test_book.id}/versions/chapter/{chapter_id}/restore/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    async_session.expire_all()
    live = await async_session.get(Chapter, chapter_id)
    assert live.summary == summary
    assert not live.planned_beats


# --- the beat's cast (review S8) --------------------------------------------------------


async def test_a_trashed_beat_cast_falls_back_but_an_explicit_unknown_422s(
    client, auth_headers, async_session, test_book
):
    from datetime import datetime, timezone

    ids, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    url = _PLANNED.format(promoted["chapter_id"])
    bogus = str(uuid.uuid4())
    r = await client.post(url, json={"character_ids": [bogus]}, headers=auth_headers)
    assert r.status_code == 422 and r.json()["detail"]["unknown"] == [bogus]

    edric = await async_session.get(Character, uuid.UUID(ids["Edric Thane"]))
    edric.deleted_at = datetime.now(timezone.utc)  # beat 1's whole own cast
    await async_session.commit()
    r = await client.post(url, json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    first = (await _requests(async_session))[0]
    assert first["character_ids"] == [ids["Milo Voss"], ids["Zara Okafor"]]


# --- a single scene from a beat (review S7) ---------------------------------------------


async def test_a_single_scene_from_a_beat_stores_its_id_and_secondaries(
    client, auth_headers, async_session, test_book
):
    ids, promoted, (_, _, b3) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    url = f"/api/v1/books/chapters/{promoted['chapter_id']}/scenes/generate"
    body = {
        "character_ids": [ids["Milo Voss"]],
        "scene_description": "Caseworker: Prava stamps the form",
        "setting": "",
        "emotional_tone": "",
    }
    r = await client.post(
        url, json={**body, "planned_beat_id": b3.upper()}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    (job,) = (await async_session.execute(select(Job))).scalars().all()
    assert job.payload["request"]["planned_beat_id"] == b3
    assert job.payload["request"]["secondary_names"] == ["Prava"]
    scene = await async_session.get(Scene, uuid.UUID(r.json()["scene_id"]))
    assert scene.scene_request["planned_beat_id"] == b3
    assert scene.scene_request["beat_title"] == "Caseworker"

    # A beat the chapter no longer has (a stale modal): stored as None.
    r = await client.post(
        url,
        json={**body, "planned_beat_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    scene = await async_session.get(Scene, uuid.UUID(r.json()["scene_id"]))
    assert scene.scene_request["planned_beat_id"] is None
    assert scene.scene_request["beat_title"] is None
    assert "secondary_names" not in scene.scene_request


async def test_the_chapter_publishes_each_beats_live_cast(
    client, auth_headers, async_session, test_book
):
    """W3-6: `cast_ids` is beat_people's cast, re-classified against the live
    cast on every read; the stored `character_ids` are not rewritten."""
    ids, promoted, _ = await _beat_book(client, auth_headers, async_session, test_book)
    prava = Character(
        book_id=test_book.id, user_id=test_book.user_id, name="Prava", role="minor"
    )
    async_session.add(prava)
    await async_session.commit()
    url = f"/api/v1/books/chapters/{promoted['chapter_id']}"
    beats = (await client.get(url, headers=auth_headers)).json()["planned_beats"]
    milo, zara = ids["Milo Voss"], ids["Zara Okafor"]
    assert [b["cast_ids"] for b in beats] == [
        [milo, ids["Edric Thane"]],
        [milo, zara],
        [milo, str(prava.id)],
    ]
    assert beats[2]["character_ids"] == [] and beats[2]["secondary_names"] == ["Prava"]
    book = (
        await client.get(f"/api/v1/books/{test_book.id}", headers=auth_headers)
    ).json()
    assert book["character_count"] == 4
    assert book["chapters"][-1]["planned_beats"][2]["cast_ids"] == [milo, str(prava.id)]


async def test_a_single_scene_and_a_batch_cast_explicit_ids_alike(
    client, auth_headers, async_session, test_book
):
    """S8: one explicit-cast rule (with_pov) for both endpoints, POV first."""
    ids, promoted, (b1, _, _) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    milo, zara = ids["Milo Voss"], ids["Zara Okafor"]
    r = await client.post(
        f"/api/v1/books/chapters/{promoted['chapter_id']}/scenes/generate",
        json={
            "character_ids": [zara, milo],
            "scene_description": "Intake: Edric reviews Milo's file",
            "setting": "",
            "emotional_tone": "",
            "planned_beat_id": b1,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    (job,) = (await async_session.execute(select(Job))).scalars().all()
    single = job.payload["request"]
    await async_session.delete(job)
    await async_session.commit()
    r = await client.post(
        _PLANNED.format(promoted["chapter_id"]),
        json={"character_ids": [zara, milo]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    batch = (await _requests(async_session))[0]
    assert single["character_ids"] == batch["character_ids"] == [milo, zara]
    assert single["pov_character"] == batch["pov_character"] == "Milo Voss"


# --- promote: one classification per node --------------------------------------------------


async def test_legacy_characters_and_people_lists_promote_the_same_beats(
    client, auth_headers, async_session, test_book
):
    from datetime import datetime, timezone

    ids = await _cast(async_session, test_book)
    gone = Character(book_id=test_book.id, user_id=test_book.user_id, name="Gone")
    async_session.add(gone)
    await async_session.commit()
    gone.deleted_at = datetime.now(timezone.utc)  # trashed since the outline
    await async_session.commit()
    child = {"title": "Stamp", "summary": "the form"}
    await _outline(
        async_session,
        test_book,
        [
            {
                "node_id": str(uuid.uuid4()),
                "title": "Legacy",
                "summary": "s",
                "character_ids": [ids["Zara Okafor"], str(gone.id)],
                "children": [{**child, "characters": ["Milo Voss", "Prava"]}],
            },
            {
                "node_id": str(uuid.uuid4()),
                "title": "Current",
                "summary": "s",
                "children": [
                    {
                        **child,
                        "character_ids": [ids["Milo Voss"], str(gone.id)],
                        "secondary_names": ["Prava"],
                    }
                ],
            },
        ],
    )
    r = await client.post(
        f"/api/v1/books/{test_book.id}/plans/promote-all", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    legacy, current = [
        await async_session.get(Chapter, uuid.UUID(c["chapter_id"]))
        for c in r.json()["chapters"]
    ]
    assert legacy.character_ids == [ids["Zara Okafor"]]  # the trashed one dropped

    def shape(beats):
        return [{k: v for k, v in b.items() if k != "node_id"} for b in beats]

    assert shape(legacy.planned_beats) == shape(current.planned_beats)
    assert legacy.planned_beats[0]["character_ids"] == [ids["Milo Voss"]]
    assert legacy.planned_beats[0]["secondary_names"] == ["Prava"]


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

    def boom(names, canon):
        calls.append(1)
        raise ValueError("bad canon")

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


async def test_a_promoted_nodes_suggestions_come_from_its_chapter_beats(
    client, auth_headers, async_session, test_book, monkeypatch
):
    """Review S1: once promoted, the chapter's beats are what generation uses,
    so their names are suggested, not the outline children's."""
    seen = []
    monkeypatch.setattr(
        secondaries,
        "suggested_characters",
        lambda names, canon: seen.append(list(names)) or [],
    )
    await _cast(async_session, test_book)
    node = {
        "node_id": str(uuid.uuid4()),
        "title": "One",
        "summary": "a",
        "secondary_names": ["Node"],
        "children": [{"title": "Beat", "secondary_names": ["Fen"]}],
    }
    await _outline(async_session, test_book, [node, {**node, "node_id": None}])
    base = f"/api/v1/books/{test_book.id}/plans"
    r = await client.post(
        f"{base}/promote", json={"node_index": 0}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    plan = (await async_session.execute(select(BookPlan))).scalar_one()
    plan.content = [
        {**n, "children": [{"title": "Beat", "secondary_names": ["Other"]}]}
        for n in plan.content
    ]
    await async_session.commit()
    seen.clear()
    await client.get(base, headers=auth_headers)
    # Promoted: the chapter's beats. Unpromoted: the outline children.
    assert seen == [["Node", "Fen", "Node", "Other"]]


# --- the beat editor reads the chapter (SPEC-35) ----------------------------------------


async def test_the_chapter_lists_its_written_beats_and_scene_titles(
    client, auth_headers, async_session, test_book
):
    """(F) GET /chapters/{id} carries `written` (beat id -> scene id) and
    each scene's title, so the editor links a written beat to its scene."""
    _, promoted, (b1, b2, _) = await _beat_book(
        client, auth_headers, async_session, test_book
    )
    url = f"/api/v1/books/chapters/{promoted['chapter_id']}"
    assert (await client.get(url, headers=auth_headers)).json()["written"] == {}
    chapter = await async_session.get(Chapter, uuid.UUID(promoted["chapter_id"]))
    scene = await add_scene(async_session, test_book, chapter, beat=b1, title="Intake")
    await async_session.commit()

    body = (await client.get(url, headers=auth_headers)).json()
    assert body["written"] == {b1: str(scene.id)}
    assert [s["title"] for s in body["scenes"]] == ["Intake"]
