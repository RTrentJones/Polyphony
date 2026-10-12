"""SPEC-13 — versioning completeness and the Trash.

Sources and chapters are versioned; deleting a character, canon entry, source,
chapter or scene is soft (it moves to the book's Trash, restorable with what it
owned) and a trashed row is invisible to every list, cast, prompt, export and
continuity read. Acceptance criteria 2 and 5 (voice preservation, purge
atomicity) need real voice_chunks and live in tests/integration/test_pg_trash.py.
"""

from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.orm_models import (
    Chapter,
    Character,
    EntityVersion,
    ExtractionRun,
    Scene,
    Source,
)

pytestmark = pytest.mark.unit

API = "/api/v1"


async def _book(client, headers, title="B"):
    r = await client.post(f"{API}/books/", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _character(client, headers, book_id, name, role=None):
    r = await client.post(
        f"{API}/characters/",
        json={"name": name, "book_id": book_id, "role": role},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _chapter(client, headers, book_id, title):
    r = await client.post(
        f"{API}/books/{book_id}/chapters", json={"title": title}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _scene(client, headers, chapter_id, content):
    r = await client.post(
        f"{API}/books/chapters/{chapter_id}/scenes",
        json={"title": content, "content": content},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["scene_id"]


async def _paste(client, headers, book_id, text, title="Notes"):
    r = await client.post(
        f"{API}/sources/paste",
        json={"title": title, "content_text": text, "book_id": book_id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _trash(client, headers, book_id):
    r = await client.get(f"{API}/books/{book_id}/trash", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _restore(client, headers, book_id, kind, eid):
    return await client.post(
        f"{API}/books/{book_id}/trash/{kind}/{eid}/restore", headers=headers
    )


async def _versions(client, headers, book_id, kind, eid):
    r = await client.get(
        f"{API}/books/{book_id}/versions/{kind}/{eid}", headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["versions"]


async def _version_content(client, headers, book_id, kind, eid, n):
    r = await client.get(
        f"{API}/books/{book_id}/versions/{kind}/{eid}/{n}", headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["content"]


class TestSourceVersioning:
    async def test_acceptance_1_two_edits_then_restore_v1_appends_v4(
        self, client, auth_headers, async_session
    ):
        """Editing a source's content twice yields v1–v3; restoring v1 appends
        v4 carrying v1's text — and offers, never runs, re-extraction."""
        bid = await _book(client, auth_headers)
        sid = await _paste(client, auth_headers, bid, "First draft of the notes.")
        for text in ("Second draft of the notes.", "Third draft of the notes."):
            r = await client.patch(
                f"{API}/sources/{sid}",
                json={"content_text": text},
                headers=auth_headers,
            )
            assert r.status_code == 200, r.text

        versions = await _versions(client, auth_headers, bid, "source", sid)
        assert [v["version_no"] for v in versions] == [3, 2, 1]
        assert [v["reason"] for v in versions] == ["edited", "edited", "created"]

        runs_before = (
            await async_session.execute(select(func.count()).select_from(ExtractionRun))
        ).scalar_one()
        r = await client.post(
            f"{API}/books/{bid}/versions/source/{sid}/restore/1", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["reextract"]["offered"] is True
        assert r.json()["reextract"]["endpoint"] == f"/api/v1/sources/{sid}/process"

        versions = await _versions(client, auth_headers, bid, "source", sid)
        assert versions[0]["version_no"] == 4
        assert versions[0]["reason"] == "restored_from:1"
        v4 = await _version_content(client, auth_headers, bid, "source", sid, 4)
        assert v4["content_text"] == "First draft of the notes."
        live = (await client.get(f"{API}/sources/{sid}", headers=auth_headers)).json()
        assert live["content_text"] == "First draft of the notes."
        assert live["word_count"] == 5
        runs_after = (
            await async_session.execute(select(func.count()).select_from(ExtractionRun))
        ).scalar_one()
        assert runs_after == runs_before  # offered, not started

    async def test_title_only_edit_is_versioned(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        sid = await _paste(client, auth_headers, bid, "Some text here.")
        r = await client.patch(
            f"{API}/sources/{sid}", json={"title": "Renamed"}, headers=auth_headers
        )
        assert r.status_code == 200
        versions = await _versions(client, auth_headers, bid, "source", sid)
        assert [v["reason"] for v in versions] == ["edited", "created"]

    async def test_pre_spec13_source_keeps_its_text_as_v1(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        """A source created before versioning has no v1; the first edit keeps
        its current text as v1 instead of overwriting it unrecorded."""
        test_source.content_text = "legacy text"
        await async_session.commit()
        r = await client.patch(
            f"{API}/sources/{test_source.id}",
            json={"content_text": "new text"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        v1 = await _version_content(
            client, auth_headers, str(test_book.id), "source", test_source.id, 1
        )
        assert v1["content_text"] == "legacy text"


class TestChapterVersioning:
    async def test_create_edit_restore(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        cid = await _chapter(client, auth_headers, bid, "Opening")
        r = await client.patch(
            f"{API}/books/chapters/{cid}",
            json={"title": "The Opening", "summary": "It begins."},
            headers=auth_headers,
        )
        assert r.status_code == 200
        versions = await _versions(client, auth_headers, bid, "chapter", cid)
        assert [v["reason"] for v in versions] == ["edited", "created"]

        r = await client.post(
            f"{API}/books/{bid}/versions/chapter/{cid}/restore/1", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        ch = (
            await client.get(f"{API}/books/chapters/{cid}", headers=auth_headers)
        ).json()
        assert (ch["title"], ch["summary"]) == ("Opening", None)
        versions = await _versions(client, auth_headers, bid, "chapter", cid)
        assert versions[0]["reason"] == "restored_from:1"

    async def test_unchanged_patch_appends_nothing(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        cid = await _chapter(client, auth_headers, bid, "Same")
        await client.patch(
            f"{API}/books/chapters/{cid}", json={"title": "Same"}, headers=auth_headers
        )
        assert len(await _versions(client, auth_headers, bid, "chapter", cid)) == 1


class TestTrashedCharacterIsInvisible:
    async def test_acceptance_3_absent_from_canon_cast_and_principals(
        self, client, auth_headers, async_session
    ):
        from app.characters.cast import resolve_cast
        from app.planning.canon import build_canon

        bid = await _book(client, auth_headers)
        milo = await _character(client, auth_headers, bid, "Milo", "protagonist")
        zara = await _character(client, auth_headers, bid, "Zara", "protagonist")
        r = await client.delete(f"{API}/characters/{zara}", headers=auth_headers)
        assert r.status_code == 204

        canon = await build_canon(async_session, UUID(bid))
        assert [c.name for c in canon.characters] == ["Milo"]
        assert "Zara" not in canon.full_block()
        assert [p["name"] for p in canon.principals()] == ["Milo"]

        # the cast picker's list, and server-side cast resolution
        listed = (
            await client.get(f"{API}/characters/?book_id={bid}", headers=auth_headers)
        ).json()["characters"]
        assert [c["id"] for c in listed] == [milo]
        with pytest.raises(HTTPException) as e:
            await resolve_cast(async_session, UUID(bid), character_ids=[UUID(zara)])
        assert e.value.status_code == 422
        with pytest.raises(HTTPException):
            await resolve_cast(async_session, UUID(bid), names=["Zara"])
        assert (
            await client.get(f"{API}/characters/{zara}", headers=auth_headers)
        ).status_code == 404

    async def test_delete_appends_a_deleted_version_and_keeps_the_row(
        self, client, auth_headers, async_session
    ):
        bid = await _book(client, auth_headers)
        cid = await _character(client, auth_headers, bid, "Milo")
        await client.delete(f"{API}/characters/{cid}", headers=auth_headers)
        versions = await _versions(client, auth_headers, bid, "character", cid)
        assert [v["reason"] for v in versions] == ["deleted", "created"]
        async_session.expire_all()
        assert (await async_session.get(Character, UUID(cid))).deleted_at is not None


class TestNameReuse:
    async def test_acceptance_4_new_character_takes_name_restore_is_409(
        self, client, auth_headers
    ):
        bid = await _book(client, auth_headers)
        old = await _character(client, auth_headers, bid, "Milo")
        await client.delete(f"{API}/characters/{old}", headers=auth_headers)
        new = await _character(client, auth_headers, bid, "Milo")  # 201: reusable

        r = await _restore(client, auth_headers, bid, "character", old)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "Milo" in detail["message"]
        assert detail["conflict"] == {"type": "character", "id": new, "name": "Milo"}
        # nothing changed: still in the Trash, the new one still live
        assert [i["id"] for i in await _trash(client, auth_headers, bid)] == [old]

        # the version-restore path names the conflict the same way
        r = await client.post(
            f"{API}/books/{bid}/versions/character/{old}/restore/1",
            headers=auth_headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["conflict"]["id"] == new

        # rename the new one, and the old one comes back
        await client.patch(
            f"{API}/characters/{new}", json={"name": "Milo II"}, headers=auth_headers
        )
        r = await _restore(client, auth_headers, bid, "character", old)
        assert r.status_code == 200, r.text
        assert await _trash(client, auth_headers, bid) == []

    async def test_canon_entry_name_reuse(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        r = await client.post(
            f"{API}/books/{bid}/canon/entries",
            json={"name": "Aeon", "category": "org"},
            headers=auth_headers,
        )
        old = r.json()["id"]
        await client.delete(
            f"{API}/books/{bid}/canon/entries/{old}", headers=auth_headers
        )
        r = await client.post(
            f"{API}/books/{bid}/canon/entries",
            json={"name": "Aeon", "category": "org"},
            headers=auth_headers,
        )
        assert r.status_code == 201
        r = await _restore(client, auth_headers, bid, "canon_entry", old)
        assert r.status_code == 409
        assert r.json()["detail"]["conflict"]["name"] == "Aeon"

    async def test_source_content_reuse_and_conflict(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        old = await _paste(client, auth_headers, bid, "Same words.", "First")
        assert (
            await client.delete(f"{API}/sources/{old}", headers=auth_headers)
        ).status_code == 204
        listed = (
            await client.get(f"{API}/sources/?book_id={bid}", headers=auth_headers)
        ).json()["sources"]
        assert listed == []
        new = await _paste(client, auth_headers, bid, "Same words.", "Second")
        r = await _restore(client, auth_headers, bid, "source", old)
        assert r.status_code == 409
        assert r.json()["detail"]["conflict"] == {
            "type": "source",
            "id": new,
            "name": "Second",
        }


class TestTrash:
    async def test_lists_what_restore_brings_back(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        cid = await _character(client, auth_headers, bid, "Milo")
        ch = await _chapter(client, auth_headers, bid, "One")
        for text in ("a b", "c d", "e f"):
            await _scene(client, auth_headers, ch, text)
        await client.delete(f"{API}/characters/{cid}", headers=auth_headers)
        r = await client.delete(
            f"{API}/books/chapters/{ch}?scenes=delete", headers=auth_headers
        )
        assert r.status_code == 204, r.text

        items = await _trash(client, auth_headers, bid)
        by_type = {i["type"]: i for i in items}
        # the chapter's scenes are listed under it, not one by one
        assert sorted(by_type) == ["chapter", "character"]
        assert by_type["chapter"]["name"] == "One"
        assert by_type["chapter"]["restores"] == {"scenes": 3}
        assert by_type["chapter"]["summary"] == "the chapter and 3 scenes"
        assert by_type["character"]["name"] == "Milo"
        assert "voice_samples" in by_type["character"]["restores"]
        assert items[0]["deleted_at"] >= items[1]["deleted_at"]

    async def test_chapter_restore_brings_its_scenes_back_in_place(
        self, client, auth_headers, async_session
    ):
        bid = await _book(client, auth_headers)
        a = await _chapter(client, auth_headers, bid, "A")
        b = await _chapter(client, auth_headers, bid, "B")
        c = await _chapter(client, auth_headers, bid, "C")
        scenes = [await _scene(client, auth_headers, b, t) for t in ("x", "y")]
        await client.delete(
            f"{API}/books/chapters/{b}?scenes=delete", headers=auth_headers
        )

        book = (await client.get(f"{API}/books/{bid}", headers=auth_headers)).json()
        assert [(x["id"], x["position"]) for x in book["chapters"]] == [(a, 0), (c, 1)]
        listed = (
            await client.get(f"{API}/books/{bid}/scenes", headers=auth_headers)
        ).json()["scenes"]
        assert listed == []
        assert (
            await client.get(f"{API}/books/chapters/{b}", headers=auth_headers)
        ).status_code == 404

        r = await _restore(client, auth_headers, bid, "chapter", b)
        assert r.status_code == 200, r.text
        book = (await client.get(f"{API}/books/{bid}", headers=auth_headers)).json()
        assert [x["id"] for x in book["chapters"]] == [a, b, c]
        assert [x["position"] for x in book["chapters"]] == [0, 1, 2]
        got = (
            await client.get(f"{API}/books/chapters/{b}", headers=auth_headers)
        ).json()
        assert [(s["id"], s["position"]) for s in got["scenes"]] == [
            (scenes[0], 0),
            (scenes[1], 1),
        ]
        reasons = (
            (
                await async_session.execute(
                    select(EntityVersion.reason).where(
                        EntityVersion.entity_id == UUID(scenes[0])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(reasons) == ["deleted", "restored"]

    async def test_scene_soft_delete_closes_gap_and_restores_to_its_place(
        self, client, auth_headers
    ):
        bid = await _book(client, auth_headers)
        ch = await _chapter(client, auth_headers, bid, "One")
        s0, s1, s2 = [await _scene(client, auth_headers, ch, t) for t in "abc"]
        assert (
            await client.delete(f"{API}/scenes/{s1}", headers=auth_headers)
        ).status_code == 204
        got = (
            await client.get(f"{API}/books/chapters/{ch}", headers=auth_headers)
        ).json()
        assert [(s["id"], s["position"]) for s in got["scenes"]] == [(s0, 0), (s2, 1)]
        assert (
            await client.get(f"{API}/scenes/{s1}", headers=auth_headers)
        ).status_code == 404
        # a new scene appends after the live ones (the trashed one holds nothing)
        s3 = await _scene(client, auth_headers, ch, "d")
        r = await _restore(client, auth_headers, bid, "scene", s1)
        assert r.status_code == 200, r.text
        got = (
            await client.get(f"{API}/books/chapters/{ch}", headers=auth_headers)
        ).json()
        assert [s["id"] for s in got["scenes"]] == [s0, s1, s2, s3]
        assert [s["position"] for s in got["scenes"]] == [0, 1, 2, 3]

    async def test_scene_of_a_trashed_chapter_restores_after_its_chapter(
        self, client, auth_headers
    ):
        bid = await _book(client, auth_headers)
        ch = await _chapter(client, auth_headers, bid, "One")
        s0 = await _scene(client, auth_headers, ch, "a")
        await _scene(client, auth_headers, ch, "b")
        await client.delete(f"{API}/scenes/{s0}", headers=auth_headers)
        await client.delete(
            f"{API}/books/chapters/{ch}?scenes=delete", headers=auth_headers
        )
        r = await _restore(client, auth_headers, bid, "scene", s0)
        assert r.status_code == 409
        assert r.json()["detail"]["conflict"] == {
            "type": "chapter",
            "id": ch,
            "name": "One",
        }

    async def test_purge_is_permanent_and_only_from_the_trash(
        self, client, auth_headers, async_session
    ):
        bid = await _book(client, auth_headers)
        cid = await _character(client, auth_headers, bid, "Milo")
        # a live character cannot be purged
        r = await client.delete(
            f"{API}/books/{bid}/trash/character/{cid}", headers=auth_headers
        )
        assert r.status_code == 404
        await client.delete(f"{API}/characters/{cid}", headers=auth_headers)
        r = await client.delete(
            f"{API}/books/{bid}/trash/character/{cid}", headers=auth_headers
        )
        assert r.status_code == 204
        async_session.expire_all()
        assert await async_session.get(Character, UUID(cid)) is None
        assert await _trash(client, auth_headers, bid) == []
        # history stays (pruning is out of scope)
        assert len(await _versions(client, auth_headers, bid, "character", cid)) == 2

    async def test_purging_a_chapter_purges_its_trashed_scenes(
        self, client, auth_headers, async_session
    ):
        bid = await _book(client, auth_headers)
        ch = await _chapter(client, auth_headers, bid, "One")
        sid = await _scene(client, auth_headers, ch, "a")
        await client.delete(
            f"{API}/books/chapters/{ch}?scenes=delete", headers=auth_headers
        )
        r = await client.delete(
            f"{API}/books/{bid}/trash/chapter/{ch}", headers=auth_headers
        )
        assert r.status_code == 204
        async_session.expire_all()
        assert await async_session.get(Chapter, UUID(ch)) is None
        assert await async_session.get(Scene, UUID(sid)) is None

    async def test_unknown_type_and_other_tenant(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        r = await client.get(
            f"{API}/books/00000000-0000-0000-0000-000000000000/trash",
            headers=auth_headers,
        )
        assert r.status_code == 404
        r = await _restore(
            client, auth_headers, bid, "book", "00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 400


class TestTrashedRowsStayOutOfReads:
    async def test_export_and_continuity_skip_trashed_chapters_and_scenes(
        self, client, auth_headers, async_session
    ):
        from app.api.plans import _collect_prose

        bid = await _book(client, auth_headers)
        keep = await _chapter(client, auth_headers, bid, "Kept")
        gone = await _chapter(client, auth_headers, bid, "Gone")
        await _scene(client, auth_headers, keep, "kept prose")
        dropped = await _scene(client, auth_headers, keep, "dropped prose")
        await _scene(client, auth_headers, gone, "gone prose")
        await client.delete(f"{API}/scenes/{dropped}", headers=auth_headers)
        await client.delete(
            f"{API}/books/chapters/{gone}?scenes=delete", headers=auth_headers
        )

        md = (
            await client.get(
                f"{API}/books/{bid}/export?format=md", headers=auth_headers
            )
        ).text
        assert "kept prose" in md
        assert "dropped prose" not in md and "gone prose" not in md
        assert "Gone" not in md

        prose = await _collect_prose(UUID(bid), None, async_session)
        assert prose == "kept prose"

    async def test_canon_listing_and_trashed_entry_restore(self, client, auth_headers):
        bid = await _book(client, auth_headers)
        eid = (
            await client.post(
                f"{API}/books/{bid}/canon/entries",
                json={"name": "Aeon", "category": "org"},
                headers=auth_headers,
            )
        ).json()["id"]
        await client.delete(
            f"{API}/books/{bid}/canon/entries/{eid}", headers=auth_headers
        )
        canon = (
            await client.get(f"{API}/books/{bid}/canon", headers=auth_headers)
        ).json()
        assert canon["entries"] == []
        items = await _trash(client, auth_headers, bid)
        assert items[0]["summary"] == "the org entry"
        assert (
            await _restore(client, auth_headers, bid, "canon_entry", eid)
        ).status_code == 200
        canon = (
            await client.get(f"{API}/books/{bid}/canon", headers=auth_headers)
        ).json()
        assert [e["id"] for e in canon["entries"]] == [eid]

    async def test_trashed_source_is_not_extractable_or_estimable(
        self, client, auth_headers, async_session
    ):
        bid = await _book(client, auth_headers)
        sid = await _paste(client, auth_headers, bid, "Some text.")
        await client.delete(f"{API}/sources/{sid}", headers=auth_headers)
        assert (
            await client.get(f"{API}/sources/{sid}", headers=auth_headers)
        ).status_code == 404
        assert (
            await client.post(f"{API}/sources/{sid}/process", headers=auth_headers)
        ).status_code == 404
        src = await async_session.get(Source, UUID(sid))
        assert src is not None and src.deleted_at is not None


class TestTrashCancelsInFlightJobs:
    async def test_trashing_a_chapter_cancels_its_scenes_jobs(
        self, client, auth_headers, async_session, test_user
    ):
        """A soft-deleted scene (or a chapter's) stops its generation: a queued
        job is cancelled now, a running one is flagged to stop at its next seam."""
        from app.core.orm_models import Job

        bid = await _book(client, auth_headers)
        ch = await _chapter(client, auth_headers, bid, "One")
        a = await _scene(client, auth_headers, ch, "a")
        b = await _scene(client, auth_headers, ch, "b")
        queued = Job(
            kind="generate_prose_scene",
            payload={},
            user_id=test_user.id,
            status="queued",
            domain_type="scene",
            domain_id=UUID(a),
        )
        running = Job(
            kind="generate_prose_scene",
            payload={},
            user_id=test_user.id,
            status="running",
            domain_type="scene",
            domain_id=UUID(b),
        )
        async_session.add_all([queued, running])
        await async_session.commit()

        r = await client.delete(
            f"{API}/books/chapters/{ch}?scenes=delete", headers=auth_headers
        )
        assert r.status_code == 204, r.text
        await async_session.refresh(queued)
        await async_session.refresh(running)
        assert queued.status == "cancelled"
        assert running.status == "running" and running.cancel_requested
