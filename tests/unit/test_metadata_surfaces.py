"""Metadata surfaces: chunk browser/editor + retrieval inspector (Phase 7)."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.unit.test_rag_store import make_store

pytestmark = pytest.mark.unit


class TestStoreChunkEditing:
    async def test_update_chunk_reembeds_and_scopes_to_character(self):
        store, session = make_store(rowcount=1)
        ok = await store.update_chunk("chunk-1", "char-1", "new dialogue text")
        assert ok is True
        assert session.committed
        # the embedder was called on the NEW text (re-embed, not stale vector)
        store.embedder.aencode_one.assert_awaited_once_with("new dialogue text")
        upd = next(e for e in session.executed if "UPDATE voice_chunks" in e[0])
        assert upd[1]["id"] == "chunk-1" and upd[1]["character_id"] == "char-1"
        assert upd[1]["wc"] == 3  # word_count recomputed

    async def test_update_missing_chunk_returns_false(self):
        store, _ = make_store(rowcount=0)
        assert await store.update_chunk("nope", "char-1", "x") is False

    async def test_delete_chunk_scoped(self):
        store, session = make_store(rowcount=1)
        assert await store.delete_chunk("chunk-1", "char-1") is True
        dele = next(e for e in session.executed if "DELETE FROM voice_chunks" in e[0])
        assert dele[1] == {"id": "chunk-1", "character_id": "char-1"}

    async def test_list_chunks_shape(self):
        rows = [
            {
                "id": "c1",
                "chunk_type": "dialogue",
                "text": "hi",
                "source_location": "manual",
                "word_count": 1,
            }
        ]
        store, _ = make_store(rows=rows)
        out = await store.list_chunks("char-1")
        assert out == [
            {
                "id": "c1",
                "chunk_type": "dialogue",
                "text": "hi",
                "source": "manual",
                "word_count": 1,
            }
        ]


def _fake_store(**methods):
    store = AsyncMock()
    for name, val in methods.items():
        getattr(store, name).return_value = val
    return store


class TestChunkAPI:
    async def test_list_edit_delete_and_inspect(
        self, client, auth_headers, test_book, test_character
    ):
        cid = str(test_character.id)
        fake = _fake_store(
            list_chunks=[
                {
                    "id": "c1",
                    "chunk_type": "dialogue",
                    "text": "hi",
                    "source": "",
                    "word_count": 1,
                }
            ],
            update_chunk=True,
            delete_chunk=True,
            retrieve_similar=[
                {
                    "text": "hi",
                    "score": 0.87,
                    "chunk_type": "dialogue",
                    "source": "",
                    "word_count": 1,
                }
            ],
        )
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            lst = await client.get(
                f"/api/v1/characters/{cid}/chunks", headers=auth_headers
            )
            assert lst.status_code == 200
            assert lst.json()["chunks"][0]["id"] == "c1"

            patched = await client.patch(
                f"/api/v1/characters/{cid}/chunks/00000000-0000-0000-0000-000000000001",
                json={"text": "edited"},
                headers=auth_headers,
            )
            assert patched.status_code == 200
            fake.update_chunk.assert_awaited()

            deleted = await client.delete(
                f"/api/v1/characters/{cid}/chunks/00000000-0000-0000-0000-000000000001",
                headers=auth_headers,
            )
            assert deleted.status_code == 204

            # retrieval inspector surfaces the score the store used to discard
            insp = await client.post(
                f"/api/v1/characters/{cid}/retrieve",
                json={"query": "how do they speak?"},
                headers=auth_headers,
            )
            assert insp.status_code == 200
            assert insp.json()["results"][0]["score"] == 0.87

    async def test_edit_missing_chunk_404(
        self, client, auth_headers, test_book, test_character
    ):
        cid = str(test_character.id)
        fake = _fake_store(update_chunk=False)
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            r = await client.patch(
                f"/api/v1/characters/{cid}/chunks/00000000-0000-0000-0000-000000000009",
                json={"text": "x"},
                headers=auth_headers,
            )
        assert r.status_code == 404


class TestVoiceSamplesBookId:
    async def test_add_voice_samples_passes_book_id(
        self, client, auth_headers, test_book, test_character
    ):
        # Regression: index_chunks became book_id-required in Phase 1, but this
        # endpoint wasn't updated — it would TypeError. Assert book_id is passed.
        cid = str(test_character.id)
        fake = _fake_store(index_chunks=2)
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            r = await client.post(
                f"/api/v1/characters/{cid}/voice-samples",
                json={"samples": ["Line one.", "Line two."], "chunk_type": "dialogue"},
                headers=auth_headers,
            )
        assert r.status_code == 200, r.text
        _, kwargs = fake.index_chunks.call_args
        assert kwargs["book_id"] == str(test_book.id)
