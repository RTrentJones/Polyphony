"""Shared-collection Qdrant store for character voice chunks.

One collection (`polyphony_chunks`) with payload indexes on character_id /
user_id / book_id, instead of a collection per character (ADR-001 §6): the
sane layout for a multi-user system on Qdrant Cloud's 1 GB free tier.
Per-character retrieval isolation is preserved via payload filters.
"""

from datetime import datetime, timezone
from typing import Optional
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.core.config import settings
from app.core.logging_config import setup_logging
from .embeddings import get_embedder

logger = setup_logging("rag.store")


class ChunkStore:
    """Vector store for character content chunks."""

    def __init__(self, client: Optional[AsyncQdrantClient] = None):
        self.collection = settings.QDRANT_COLLECTION
        self.client = client or AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
        )
        self.embedder = get_embedder()

    async def ensure_collection(self) -> None:
        """Create the shared collection + payload indexes if missing."""
        existing = {c.name for c in (await self.client.get_collections()).collections}
        if self.collection in existing:
            return
        await self.client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(
                size=self.embedder.dimension, distance=Distance.COSINE
            ),
        )
        for field_name in ("character_id", "user_id", "book_id", "chunk_type"):
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        logger.info(f"Created Qdrant collection {self.collection}")

    async def index_chunks(
        self,
        character_id: str,
        character_name: str,
        user_id: str,
        chunks: list[dict],
        book_id: Optional[str] = None,
        batch_size: int = 100,
    ) -> int:
        """Index content chunks for a character. Returns count indexed."""
        if not chunks:
            return 0
        await self.ensure_collection()

        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            vectors = await self.embedder.aencode([c["text"] for c in batch])
            points = [
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "character_id": character_id,
                        "character_name": character_name,
                        "user_id": user_id,
                        "book_id": book_id or "",
                        "chunk_type": chunk.get("chunk_type", "unknown"),
                        "text": chunk["text"],
                        "source_location": chunk.get("source_location", ""),
                        "word_count": len(chunk["text"].split()),
                        "timestamp": datetime.now(timezone.utc).timestamp(),
                    },
                )
                for chunk, vector in zip(batch, vectors)
            ]
            await self.client.upsert(collection_name=self.collection, points=points)
            total += len(points)
        return total

    async def retrieve_similar(
        self,
        character_id: str,
        query: str,
        k: Optional[int] = None,
        chunk_type: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> list[dict]:
        """Retrieve a character's most similar chunks for voice grounding."""
        must = [
            FieldCondition(key="character_id", match=MatchValue(value=character_id))
        ]
        if chunk_type:
            must.append(
                FieldCondition(key="chunk_type", match=MatchValue(value=chunk_type))
            )
        try:
            query_vector = await self.embedder.aencode_one(query)
            results = await self.client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                limit=k or settings.RAG_TOP_K,
                query_filter=Filter(must=must),
                score_threshold=(
                    settings.RAG_SCORE_THRESHOLD
                    if score_threshold is None
                    else score_threshold
                ),
            )
        except Exception as e:
            # Retrieval failures degrade generation quality, not availability.
            logger.warning(
                f"RAG retrieval failed for character {character_id}: {e}",
                extra_fields={"event": "rag_retrieval_failed"},
            )
            return []
        return [
            {
                "text": hit.payload["text"],
                "score": hit.score,
                "chunk_type": hit.payload.get("chunk_type", "unknown"),
                "source": hit.payload.get("source_location", ""),
                "word_count": hit.payload.get("word_count", 0),
            }
            for hit in results
        ]

    async def character_statistics(self, character_id: str) -> dict:
        """Chunk counts / word totals for one character."""
        try:
            points, _ = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="character_id", match=MatchValue(value=character_id)
                        )
                    ]
                ),
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:
            return {"character_id": character_id, "total_chunks": 0, "error": str(e)}

        type_counts: dict[str, int] = {}
        total_words = 0
        for point in points:
            chunk_type = point.payload.get("chunk_type", "unknown")
            type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1
            total_words += point.payload.get("word_count", 0)
        return {
            "character_id": character_id,
            "total_chunks": len(points),
            "type_distribution": type_counts,
            "total_words": total_words,
        }

    async def delete_character(self, character_id: str) -> None:
        """Remove all of a character's points (character deleted / re-index)."""
        await self.client.delete(
            collection_name=self.collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="character_id", match=MatchValue(value=character_id)
                        )
                    ]
                )
            ),
        )

    async def healthy(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False


_store: Optional[ChunkStore] = None


def get_chunk_store() -> ChunkStore:
    global _store
    if _store is None:
        _store = ChunkStore()
    return _store
