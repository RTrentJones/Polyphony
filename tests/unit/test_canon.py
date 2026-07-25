"""Canon entities: CRUD, versioning, and reaching the assembled canon (Phase 3)."""

import types

import pytest

from app.planning.canon import render_bible, render_canon_entries, render_style

pytestmark = pytest.mark.unit


class TestRenderBible:
    """The deterministic proof that canon entries + style reach the model."""

    def test_render_bible_includes_cast_canon_and_style(self):
        char = types.SimpleNamespace(
            name="Milo Voss",
            role="protagonist",
            description="a dead operations analyst",
            goals="quit the afterlife",
            arc="",
            notes="",
            personality_traits=None,
            voice_characteristics=None,
            relationships=None,
        )
        entry = types.SimpleNamespace(
            name="Aeon Holdings", category="org", content="the afterlife-services firm"
        )
        style = types.SimpleNamespace(
            pov="third-limited",
            tense="past",
            tone="dry, deadpan",
            comps="Severance",
            sample_prose="",
        )
        bible = render_bible([char], [entry], style)
        assert "Milo Voss" in bible and "dead operations analyst" in bible
        assert "goals" not in bible.lower() or "quit the afterlife" in bible
        assert "Aeon Holdings" in bible and "afterlife-services firm" in bible
        assert "third-limited" in bible and "deadpan" in bible

    def test_empty_pieces_render_empty(self):
        assert render_canon_entries([]) == ""
        assert render_style(None) == ""
        assert render_bible([], [], None) == ""

    def test_entries_grouped_by_category(self):
        entries = [
            types.SimpleNamespace(
                name="Marrow District", category="location", content="offices"
            ),
            types.SimpleNamespace(name="Aeon", category="org", content="the firm"),
        ]
        out = render_canon_entries(entries)
        assert "## Locations" in out and "## Organizations" in out
        assert out.index("Locations") < out.index("Organizations")  # world-order


class TestCanonEntryCRUD:
    async def test_create_list_edit_delete(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        r = await client.post(
            f"/api/v1/books/{bid}/canon/entries",
            json={
                "name": "The Undeath Pipeline",
                "category": "concept",
                "content": "refines grief",
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        eid = r.json()["id"]

        got = await client.get(f"/api/v1/books/{bid}/canon", headers=auth_headers)
        assert got.status_code == 200
        names = {e["name"] for e in got.json()["entries"]}
        assert "The Undeath Pipeline" in names

        p = await client.patch(
            f"/api/v1/books/{bid}/canon/entries/{eid}",
            json={"content": "refines grief into shareholder value"},
            headers=auth_headers,
        )
        assert p.status_code == 200
        assert "shareholder" in p.json()["content"]

        d = await client.delete(
            f"/api/v1/books/{bid}/canon/entries/{eid}", headers=auth_headers
        )
        assert d.status_code == 204
        after = await client.get(f"/api/v1/books/{bid}/canon", headers=auth_headers)
        assert after.json()["entries"] == []

    async def test_invalid_category_400(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        r = await client.post(
            f"/api/v1/books/{bid}/canon/entries",
            json={"name": "X", "category": "nonsense"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    async def test_duplicate_name_409(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        body = {"name": "Aeon Holdings", "category": "org"}
        assert (
            await client.post(
                f"/api/v1/books/{bid}/canon/entries", json=body, headers=auth_headers
            )
        ).status_code == 201
        dup = await client.post(
            f"/api/v1/books/{bid}/canon/entries", json=body, headers=auth_headers
        )
        assert dup.status_code == 409

    async def test_edits_are_versioned(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        eid = (
            await client.post(
                f"/api/v1/books/{bid}/canon/entries",
                json={"name": "CEL", "category": "concept", "content": "v1"},
                headers=auth_headers,
            )
        ).json()["id"]
        await client.patch(
            f"/api/v1/books/{bid}/canon/entries/{eid}",
            json={"content": "v2"},
            headers=auth_headers,
        )
        versions = (
            await client.get(
                f"/api/v1/books/{bid}/versions/canon_entry/{eid}", headers=auth_headers
            )
        ).json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]

        # restore v1 -> content back to "v1"
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/canon_entry/{eid}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text
        v3 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/canon_entry/{eid}/3",
                headers=auth_headers,
            )
        ).json()
        assert v3["content"]["content"] == "v1"


class TestStyleGuide:
    async def test_upsert_and_version(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        r = await client.put(
            f"/api/v1/books/{bid}/canon/style",
            json={"pov": "third-limited", "tense": "past", "tone": "dry"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        style_id = r.json()["id"]
        assert r.json()["pov"] == "third-limited"

        # editing keeps the same single row
        r2 = await client.put(
            f"/api/v1/books/{bid}/canon/style",
            json={"tone": "dry, deadpan"},
            headers=auth_headers,
        )
        assert r2.json()["id"] == style_id
        assert r2.json()["pov"] == "third-limited"  # unchanged
        assert r2.json()["tone"] == "dry, deadpan"

        versions = (
            await client.get(
                f"/api/v1/books/{bid}/versions/style_guide/{style_id}",
                headers=auth_headers,
            )
        ).json()["versions"]
        assert [v["reason"] for v in versions] == ["edited", "created"]


class TestCrossTenant:
    async def test_other_user_cannot_read_canon(
        self, client, async_session, auth_headers, test_book
    ):
        from app.core.orm_models import User
        from app.core.security import create_access_token, get_password_hash

        bid = str(test_book.id)
        other = User(
            email="thief2@example.com",
            hashed_password=get_password_hash("password123"),
            full_name="Thief",
        )
        async_session.add(other)
        await async_session.commit()
        await async_session.refresh(other)
        headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
        }
        resp = await client.get(f"/api/v1/books/{bid}/canon", headers=headers)
        assert resp.status_code == 404
