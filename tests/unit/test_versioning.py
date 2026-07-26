"""Versioning: append-only entity_versions, forward-only restore (ADR-002 §5).

The load-bearing invariant: the log INCLUDES head, so max(version_no) always
equals the live row, and restore APPENDS (never rewinds/deletes). These lock
"regeneration/edits never clobber".
"""

from uuid import UUID, uuid4

import pytest

from app.core.orm_models import EntityVersion
from app.versioning import repository as vr

pytestmark = pytest.mark.unit


class TestRepository:
    async def test_snapshot_increments_and_lists_newest_first(
        self, async_session, test_book
    ):
        eid = uuid4()
        v1 = await vr.snapshot(
            async_session,
            book_id=test_book.id,
            entity_type="book_plan",
            entity_id=eid,
            content={"n": 1},
            reason="created",
        )
        v2 = await vr.snapshot(
            async_session,
            book_id=test_book.id,
            entity_type="book_plan",
            entity_id=eid,
            content={"n": 2},
            reason="edited",
        )
        await async_session.commit()
        assert (v1.version_no, v2.version_no) == (1, 2)

        versions = await vr.list_versions(async_session, "book_plan", eid)
        assert [v.version_no for v in versions] == [2, 1]
        assert (await vr.latest(async_session, "book_plan", eid)).version_no == 2

    async def test_versions_are_isolated_by_entity(self, async_session, test_book):
        a, b = uuid4(), uuid4()
        await vr.snapshot(
            async_session,
            book_id=test_book.id,
            entity_type="character",
            entity_id=a,
            content={},
            reason="created",
        )
        vb = await vr.snapshot(
            async_session,
            book_id=test_book.id,
            entity_type="character",
            entity_id=b,
            content={},
            reason="created",
        )
        await async_session.commit()
        # b's numbering starts at 1 too — entities don't share a counter.
        assert vb.version_no == 1


class TestPlanVersioning:
    async def test_generate_and_edit_accumulate_versions_then_restore(
        self, client, auth_headers, test_book
    ):
        bid = str(test_book.id)
        # v1: create
        r = await client.put(
            f"/api/v1/books/{bid}/plans",
            json={"kind": "outline", "content": [{"title": "Ch1", "summary": "alpha"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        plan_id = r.json()["id"]
        # v2: edit
        r = await client.put(
            f"/api/v1/books/{bid}/plans",
            json={"kind": "outline", "content": [{"title": "Ch1x", "summary": "beta"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200

        lv = await client.get(
            f"/api/v1/books/{bid}/versions/book_plan/{plan_id}", headers=auth_headers
        )
        versions = lv.json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]  # newest first
        assert versions[0]["reason"] == "edited"
        assert versions[1]["reason"] == "created"

        # restore v1 -> appends v3 (forward-only), live content reverts
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/book_plan/{plan_id}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text

        plans = await client.get(f"/api/v1/books/{bid}/plans", headers=auth_headers)
        content = plans.json()["plans"][0]["content"]
        assert content[0]["title"] == "Ch1"  # reverted to v1

        lv2 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/book_plan/{plan_id}",
                headers=auth_headers,
            )
        ).json()["versions"]
        assert lv2[0]["version_no"] == 3  # max == live, always
        assert lv2[0]["reason"] == "restored_from:1"

    async def test_restore_invalid_content_409_not_corrupt(
        self, client, auth_headers, async_session, test_book
    ):
        bid = str(test_book.id)
        r = await client.put(
            f"/api/v1/books/{bid}/plans",
            json={"kind": "outline", "content": [{"title": "Good", "summary": "s"}]},
            headers=auth_headers,
        )
        plan_id = r.json()["id"]
        # Inject a malformed historical version directly — a shape today's
        # validator rejects (validate_outline_nodes requires a list of nodes).
        async_session.add(
            EntityVersion(
                book_id=test_book.id,
                entity_type="book_plan",
                entity_id=UUID(plan_id),
                version_no=99,
                content={"kind": "outline", "content": "not-a-list-anymore"},
                reason="edited",
            )
        )
        await async_session.commit()

        rr = await client.post(
            f"/api/v1/books/{bid}/versions/book_plan/{plan_id}/restore/99",
            headers=auth_headers,
        )
        assert rr.status_code == 409, rr.text
        # live plan untouched
        content = (
            await client.get(f"/api/v1/books/{bid}/plans", headers=auth_headers)
        ).json()["plans"][0]["content"]
        assert content[0]["title"] == "Good"


class TestCharacterVersioning:
    async def test_create_edit_restore(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        c = await client.post(
            "/api/v1/characters/",
            json={"name": "Milo", "book_id": bid, "role": "protagonist"},
            headers=auth_headers,
        )
        assert c.status_code == 201, c.text
        cid = c.json()["id"]
        # edit role
        await client.patch(
            f"/api/v1/characters/{cid}",
            json={"role": "antagonist"},
            headers=auth_headers,
        )
        lv = (
            await client.get(
                f"/api/v1/books/{bid}/versions/character/{cid}", headers=auth_headers
            )
        ).json()["versions"]
        assert [v["version_no"] for v in lv] == [2, 1]

        # restore v1 -> role back to protagonist (verified via the appended v3 snapshot)
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/character/{cid}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text
        v3 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/character/{cid}/3", headers=auth_headers
            )
        ).json()
        assert v3["reason"] == "restored_from:1"
        assert v3["content"]["role"] == "protagonist"  # live row reverted


class TestCrossTenant:
    async def test_other_user_cannot_read_versions(
        self, client, async_session, auth_headers, test_book
    ):
        from app.core.orm_models import User
        from app.core.security import create_access_token, get_password_hash

        bid = str(test_book.id)
        r = await client.put(
            f"/api/v1/books/{bid}/plans",
            json={"kind": "outline", "content": [{"title": "Secret", "summary": "s"}]},
            headers=auth_headers,
        )
        plan_id = r.json()["id"]

        other = User(
            email="thief@example.com",
            hashed_password=get_password_hash("password123"),
            full_name="Thief",
        )
        async_session.add(other)
        await async_session.commit()
        await async_session.refresh(other)
        headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
        }
        # book isn't theirs -> 404 (never leak another tenant's history)
        resp = await client.get(
            f"/api/v1/books/{bid}/versions/book_plan/{plan_id}", headers=headers
        )
        assert resp.status_code == 404
