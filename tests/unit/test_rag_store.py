"""Unit tests for the shared-collection Qdrant store."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.embeddings import cosine_similarity
from app.rag.store import ChunkStore


def make_store() -> ChunkStore:
    client = AsyncMock()
    with patch("app.rag.store.get_embedder") as get_embedder:
        embedder = MagicMock()
        embedder.dimension = 384
        embedder.aencode = AsyncMock(
            side_effect=lambda texts: [[0.1] * 384 for _ in texts]
        )
        embedder.aencode_one = AsyncMock(return_value=[0.1] * 384)
        get_embedder.return_value = embedder
        store = ChunkStore(client=client)
    return store


@pytest.mark.unit
class TestChunkStore:
    @pytest.mark.asyncio
    async def test_index_chunks_batches_and_payloads(self):
        store = make_store()
        store.client.get_collections.return_value = MagicMock(collections=[])

        chunks = [
            {"text": "Hello there", "chunk_type": "dialogue", "source_location": "p1"},
            {"text": "She ran off", "chunk_type": "action", "source_location": "p2"},
        ]
        count = await store.index_chunks(
            character_id="char-1",
            character_name="Alice",
            user_id="user-1",
            chunks=chunks,
        )
        assert count == 2
        store.client.upsert.assert_awaited_once()
        points = store.client.upsert.await_args.kwargs["points"]
        assert points[0].payload["character_id"] == "char-1"
        assert points[0].payload["user_id"] == "user-1"
        assert points[1].payload["chunk_type"] == "action"

    @pytest.mark.asyncio
    async def test_index_empty_is_zero(self):
        store = make_store()
        assert (
            await store.index_chunks(
                character_id="c", character_name="n", user_id="u", chunks=[]
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_retrieve_filters_by_character(self):
        store = make_store()
        hit = MagicMock(score=0.9)
        hit.payload = {
            "text": "sample",
            "chunk_type": "dialogue",
            "source_location": "p1",
            "word_count": 1,
        }
        store.client.search.return_value = [hit]

        results = await store.retrieve_similar(character_id="char-1", query="query")
        assert results[0]["text"] == "sample"
        query_filter = store.client.search.await_args.kwargs["query_filter"]
        keys = [c.key for c in query_filter.must]
        assert "character_id" in keys

    @pytest.mark.asyncio
    async def test_retrieve_failure_returns_empty(self):
        store = make_store()
        store.client.search.side_effect = RuntimeError("qdrant down")
        assert await store.retrieve_similar(character_id="c", query="q") == []


@pytest.mark.unit
class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
