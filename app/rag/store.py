"""pgvector-backed store for character voice chunks.

Vector search lives in the SAME Postgres (Neon) database as everything else:
a `voice_chunks` table with a vector(384) column, cosine distance, HNSW index
(ADR-001 amendment — replaces the earlier Qdrant Cloud design; one store,
zero extra accounts). The table is created by the Alembic baseline on
PostgreSQL only and deliberately kept OFF the ORM Base so sqlite test
databases never see a vector column; queries here are raw SQL.

Per-character retrieval isolation is a WHERE clause; `ON DELETE CASCADE`
from characters keeps vectors consistent with the bible.
"""

from typing import Optional
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.logging_config import setup_logging
from .embeddings import get_embedder

logger = setup_logging("rag.store")


def _vector_literal(vector: list[float]) -> str:
    """pgvector accepts '[x,y,...]'::vector — bind as text, cast in SQL."""
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


class ChunkStore:
    """Vector store for character content chunks (pgvector)."""

    def __init__(self, session_factory=None):
        # session_factory: zero-arg callable returning an async session context
        # (tests inject a sqlite/mock factory). Default resolves the app's
        # factory lazily so importing this module never requires DB config.
        self._session_factory = session_factory
        self.embedder = get_embedder()

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from app.core.database import get_session_factory

        return get_session_factory()()

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

        total = 0
        async with self._session() as session:
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                vectors = await self.embedder.aencode([c["text"] for c in batch])
                for chunk, vector in zip(batch, vectors):
                    await session.execute(
                        text(
                            """
                            INSERT INTO voice_chunks
                              (id, character_id, user_id, book_id, chunk_type,
                               text, source_location, word_count, embedding)
                            VALUES
                              (:id, :character_id, :user_id, :book_id, :chunk_type,
                               :text, :source_location, :word_count,
                               CAST(:embedding AS vector))
                            """
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "character_id": character_id,
                            "user_id": user_id,
                            "book_id": book_id,
                            "chunk_type": chunk.get("chunk_type", "unknown"),
                            "text": chunk["text"],
                            "source_location": chunk.get("source_location", ""),
                            "word_count": len(chunk["text"].split()),
                            "embedding": _vector_literal(vector),
                        },
                    )
                total += len(batch)
            await session.commit()
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
        threshold = (
            settings.RAG_SCORE_THRESHOLD if score_threshold is None else score_threshold
        )
        try:
            query_vector = _vector_literal(await self.embedder.aencode_one(query))
            sql = """
                SELECT text, chunk_type, source_location, word_count,
                       1 - (embedding <=> CAST(:q AS vector)) AS score
                FROM voice_chunks
                WHERE character_id = :character_id
            """
            params: dict = {
                "q": query_vector,
                "character_id": character_id,
                "k": k or settings.RAG_TOP_K,
            }
            if chunk_type:
                sql += " AND chunk_type = :chunk_type"
                params["chunk_type"] = chunk_type
            sql += " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"

            async with self._session() as session:
                rows = (await session.execute(text(sql), params)).mappings().all()
        except Exception as e:
            # Retrieval failures degrade generation quality, not availability.
            logger.warning(
                f"RAG retrieval failed for character {character_id}: {e}",
                extra_fields={"event": "rag_retrieval_failed"},
            )
            return []

        return [
            {
                "text": row["text"],
                "score": float(row["score"]),
                "chunk_type": row["chunk_type"],
                "source": row["source_location"] or "",
                "word_count": row["word_count"] or 0,
            }
            for row in rows
            if row["score"] is None or float(row["score"]) >= threshold
        ]

    async def character_statistics(self, character_id: str) -> dict:
        """Chunk counts / word totals for one character."""
        try:
            async with self._session() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                """
                                SELECT chunk_type, COUNT(*) AS n,
                                       COALESCE(SUM(word_count), 0) AS words
                                FROM voice_chunks
                                WHERE character_id = :character_id
                                GROUP BY chunk_type
                                """
                            ),
                            {"character_id": character_id},
                        )
                    )
                    .mappings()
                    .all()
                )
        except Exception as e:
            return {"character_id": character_id, "total_chunks": 0, "error": str(e)}

        type_counts = {row["chunk_type"]: int(row["n"]) for row in rows}
        return {
            "character_id": character_id,
            "total_chunks": sum(type_counts.values()),
            "type_distribution": type_counts,
            "total_words": sum(int(row["words"]) for row in rows),
        }

    async def delete_character(self, character_id: str) -> None:
        """Remove a character's vectors (explicit re-index; CASCADE covers deletes)."""
        async with self._session() as session:
            await session.execute(
                text("DELETE FROM voice_chunks WHERE character_id = :character_id"),
                {"character_id": character_id},
            )
            await session.commit()

    async def healthy(self) -> bool:
        """True when the DB answers and the pgvector extension is installed."""
        try:
            async with self._session() as session:
                row = (
                    await session.execute(
                        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                    )
                ).first()
            return row is not None
        except Exception:
            return False


_store: Optional[ChunkStore] = None


def get_chunk_store() -> ChunkStore:
    global _store
    if _store is None:
        _store = ChunkStore()
    return _store


def reset_chunk_store() -> None:
    """Test hook."""
    global _store
    _store = None
