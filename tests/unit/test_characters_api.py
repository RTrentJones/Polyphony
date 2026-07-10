"""Characters API: manual creation (no manuscript), listing, ownership."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.orm_models import Character, User
from app.core.security import create_access_token, get_password_hash


@pytest.mark.unit
class TestCharacterCreation:
    async def test_create_without_manuscript(self, client, auth_headers):
        """A fresh account can create a character before uploading anything."""
        resp = await client.post(
            "/api/v1/characters/",
            json={
                "name": "Imogen",
                "description": "A cartographer",
                "role": "protagonist",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Imogen"

    async def test_create_with_owned_manuscript(
        self, client, auth_headers, test_manuscript
    ):
        resp = await client.post(
            "/api/v1/characters/",
            json={"name": "Quill", "manuscript_id": str(test_manuscript.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text

    async def test_create_with_foreign_manuscript_404s(
        self, client, async_session, test_manuscript
    ):
        other = User(
            email="other@example.com",
            hashed_password=get_password_hash("otherpassword1"),
            full_name="Other",
        )
        async_session.add(other)
        await async_session.commit()
        await async_session.refresh(other)
        headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': str(other.id)})}"
        }
        resp = await client.post(
            "/api/v1/characters/",
            json={"name": "Thief", "manuscript_id": str(test_manuscript.id)},
            headers=headers,
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestCharacterListing:
    async def test_list_includes_manual_and_extracted(
        self, client, auth_headers, async_session, test_user, test_character
    ):
        manual = Character(user_id=test_user.id, name="Manual Marta")
        async_session.add(manual)
        await async_session.commit()

        resp = await client.get("/api/v1/characters/", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        names = {c["name"] for c in resp.json()["characters"]}
        # test_character is owned via its manuscript (legacy path, no user_id);
        # Manual Marta directly via user_id — both must appear.
        assert {"Manual Marta", "Test Character"} <= names

    async def test_list_excludes_other_users(self, client, async_session, test_user):
        mine = Character(user_id=test_user.id, name="Mine")
        other = User(
            email="stranger@example.com",
            hashed_password=get_password_hash("strangerpass1"),
            full_name="Stranger",
        )
        async_session.add_all([mine, other])
        await async_session.commit()
        await async_session.refresh(other)
        async_session.add(Character(user_id=other.id, name="Not Mine"))
        await async_session.commit()

        headers = {
            "Authorization": f"Bearer {create_access_token(data={'sub': str(test_user.id)})}"
        }
        resp = await client.get("/api/v1/characters/", headers=headers)
        names = {c["name"] for c in resp.json()["characters"]}
        assert "Mine" in names
        assert "Not Mine" not in names


@pytest.mark.unit
class TestManualCharacterLifecycle:
    async def test_get_patch_delete_manual_character(
        self, client, auth_headers, async_session, test_user
    ):
        created = await client.post(
            "/api/v1/characters/",
            json={"name": "Ephemeral"},
            headers=auth_headers,
        )
        cid = created.json()["id"]

        stats = {"total_chunks": 0, "by_type": {}}
        with patch("app.api.characters.get_chunk_store") as store:
            store.return_value.character_statistics = AsyncMock(return_value=stats)
            got = await client.get(f"/api/v1/characters/{cid}", headers=auth_headers)
        assert got.status_code == 200, got.text
        assert got.json()["manuscript_id"] is None

        patched = await client.patch(
            f"/api/v1/characters/{cid}",
            json={"role": "antagonist", "goals": "Win."},
            headers=auth_headers,
        )
        assert patched.status_code == 200, patched.text

        with patch("app.api.characters.get_chunk_store") as store:
            store.return_value.delete_character = AsyncMock(return_value=None)
            deleted = await client.delete(
                f"/api/v1/characters/{cid}", headers=auth_headers
            )
        assert deleted.status_code == 204
