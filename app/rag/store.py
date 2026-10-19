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

from typing import Callable, Optional
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.logging_config import setup_logging
from .embeddings import get_embedder

logger = setup_logging("rag.store")


def _vector_literal(vector: list[float]) -> str:
    """pgvector accepts '[x,y,...]'::vector — bind as text, cast in SQL."""
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


def _chunk_row(row) -> dict:
    """The browser/editor shape of one voice_chunks row."""
    created = row.get("created_at")
    return {
        "id": str(row["id"]),
        "source_id": str(row["source_id"]) if row.get("source_id") else None,
        "chunk_type": row["chunk_type"],
        "text": row["text"],
        "source": row["source_location"] or "",
        "word_count": row["word_count"] or 0,
        "created_at": (
            created.isoformat() if hasattr(created, "isoformat") else created
        ),
    }


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
        book_id: str,
        chunks: list[dict],
        source_id: Optional[str] = None,
        batch_size: int = 100,
    ) -> int:
        """Index content chunks for a character. Returns count indexed.

        `book_id` is required (no default): voice chunks are book-rooted like
        their character, and a missing book_id must fail loudly at the call site
        (TypeError) rather than silently writing a NULL that the book-scoped
        retrieval filter would then never match (docs/ADR-002-book-as-root.md §1).

        `source_id` records voice PROVENANCE on the chunk so a character can carry
        voice from multiple sources and each source is re-indexable in isolation
        (PR review #2).
        """
        if not chunks:
            return 0

        total = 0
        async with self._session() as session:
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                vectors = await self.embedder.aencode([c["text"] for c in batch])
                for chunk, vector in zip(batch, vectors):
                    await session.execute(
                        text("""
                            INSERT INTO voice_chunks
                              (id, character_id, user_id, book_id, source_id,
                               chunk_type, text, source_location, word_count,
                               embedding)
                            VALUES
                              (:id, :character_id, :user_id, :book_id, :source_id,
                               :chunk_type, :text, :source_location, :word_count,
                               CAST(:embedding AS vector))
                            """),
                        {
                            "id": str(uuid.uuid4()),
                            "character_id": character_id,
                            "user_id": user_id,
                            "book_id": book_id,
                            "source_id": source_id,
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

    async def delete_source_chunks(self, character_id: str, source_id: str) -> None:
        """Remove only THIS source's chunks for a character — idempotent per-source
        re-indexing that preserves manual and other-source voice (PR review #2)."""
        async with self._session() as session:
            await session.execute(
                text(
                    "DELETE FROM voice_chunks "
                    "WHERE character_id = :character_id AND source_id = :source_id"
                ),
                {"character_id": character_id, "source_id": source_id},
            )
            await session.commit()

    async def retrieve_similar(
        self,
        character_id: str,
        query: str,
        k: Optional[int] = None,
        chunk_type: Optional[str] = None,
        score_threshold: Optional[float] = None,
        user_id: Optional[str] = None,
        book_id: Optional[str] = None,
        keep: Optional[Callable[[dict], bool]] = None,
        exhausted: Optional[list] = None,
    ) -> list[dict]:
        """Retrieve a character's most similar chunks for voice grounding.

        `keep` (the caller's eligibility) filters the rows before the threshold,
        so its fallback returns the closest *eligible* rows (SPEC-40).

        Pass user_id and/or book_id for defense-in-depth: even though
        character_id already implies one owner and one book, scoping the vector
        read by them too means a mis-scoped caller can never read another
        tenant's — or another book's — voice.
        """
        threshold = (
            settings.RAG_SCORE_THRESHOLD if score_threshold is None else score_threshold
        )
        try:
            query_vector = _vector_literal(await self.embedder.aencode_one(query))
            sql = """
                SELECT id, source_id, text, chunk_type, source_location, word_count,
                       1 - (embedding <=> CAST(:q AS vector)) AS score
                FROM voice_chunks
                WHERE character_id = :character_id
                  AND NOT EXISTS (
                      SELECT 1 FROM characters c
                      WHERE c.id = voice_chunks.character_id
                        AND c.deleted_at IS NOT NULL
                  )
            """
            # A trashed character's chunks stay (restore brings the voice back,
            # SPEC-13) but never ground anything while it is in the Trash.
            params: dict = {
                "q": query_vector,
                "character_id": character_id,
                "k": k or settings.RAG_TOP_K,
            }
            if user_id:
                sql += " AND user_id = :user_id"
                params["user_id"] = user_id
            if book_id:
                sql += " AND book_id = :book_id"
                params["book_id"] = book_id
            if chunk_type:
                sql += " AND chunk_type = :chunk_type"
                params["chunk_type"] = chunk_type
            sql += " ORDER BY embedding <=> CAST(:q AS vector), id LIMIT :k"

            async with self._session() as session:
                rows = (await session.execute(text(sql), params)).mappings().all()
        except Exception as e:
            # Retrieval failures degrade generation quality, not availability.
            logger.warning(
                f"RAG retrieval failed for character {character_id}: {e}",
                extra_fields={"event": "rag_retrieval_failed"},
            )
            return []

        if exhausted is not None and len(rows) >= params["k"]:
            exhausted.append(character_id)  # the bound was hit (SPEC-83)
        results = [
            {
                "id": str(row["id"]) if row.get("id") is not None else None,
                "source_id": str(row["source_id"]) if row.get("source_id") else None,
                "text": row["text"],
                "score": float(row["score"]) if row["score"] is not None else None,
                "chunk_type": row["chunk_type"],
                "source": row["source_location"] or "",
                "word_count": row["word_count"] or 0,
            }
            for row in rows
        ]
        return above_threshold(
            [r for r in results if keep is None or keep(r)], threshold
        )

    async def character_statistics(self, character_id: str) -> dict:
        """Chunk counts / word totals for one character."""
        try:
            async with self._session() as session:
                rows = (
                    (
                        await session.execute(
                            text("""
                                SELECT chunk_type, COUNT(*) AS n,
                                       COALESCE(SUM(word_count), 0) AS words
                                FROM voice_chunks
                                WHERE character_id = :character_id
                                GROUP BY chunk_type
                                """),
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

    @staticmethod
    def _chunk_filters(
        character_id: str, source_id: Optional[str], chunk_type: Optional[str]
    ) -> tuple[str, dict]:
        where = "WHERE character_id = :character_id"
        params: dict = {"character_id": character_id}
        if source_id:
            where += " AND source_id = :source_id"
            params["source_id"] = source_id
        if chunk_type:
            where += " AND chunk_type = :chunk_type"
            params["chunk_type"] = chunk_type
        return where, params

    async def list_chunks(
        self,
        character_id: str,
        *,
        skip: int = 0,
        limit: Optional[int] = None,
        source_id: Optional[str] = None,
        chunk_type: Optional[str] = None,
    ) -> list[dict]:
        """A character's voice chunks for the browser/editor, oldest first.

        `skip`/`limit` page through a character with thousands of chunks (a
        manuscript-scale extraction) instead of shipping them all to the UI.
        """
        where, params = self._chunk_filters(character_id, source_id, chunk_type)
        sql = (
            "SELECT id, source_id, chunk_type, text, source_location, word_count, "
            f"created_at FROM voice_chunks {where} ORDER BY created_at, id"
        )
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        if skip:
            sql += " OFFSET :skip"
            params["skip"] = skip
        async with self._session() as session:
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [_chunk_row(row) for row in rows]

    async def count_chunks(
        self,
        character_id: str,
        *,
        source_id: Optional[str] = None,
        chunk_type: Optional[str] = None,
    ) -> int:
        """How many chunks `list_chunks` would page through (same filters)."""
        where, params = self._chunk_filters(character_id, source_id, chunk_type)
        async with self._session() as session:
            row = (
                await session.execute(
                    text(f"SELECT COUNT(*) AS n FROM voice_chunks {where}"), params
                )
            ).first()
        return int(row[0]) if row else 0

    async def get_chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        """Current state of the given chunks, keyed by id; missing ids are absent.

        Resolves a scene's stored grounding (chunk ids + scores) to the text the
        chunks hold NOW, so an edited sample shows its edit and a deleted one
        shows as gone (R5.3).
        """
        ids = [i for i in dict.fromkeys(chunk_ids) if i]
        if not ids:
            return {}
        params = {f"id{n}": i for n, i in enumerate(ids)}
        placeholders = ", ".join(f"CAST(:{k} AS uuid)" for k in params)
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, character_id, source_id, chunk_type, text, "
                            "source_location, word_count, created_at "
                            f"FROM voice_chunks WHERE id IN ({placeholders})"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return {
            str(row["id"]): {
                **_chunk_row(row),
                "character_id": str(row["character_id"]),
            }
            for row in rows
        }

    async def update_chunk(
        self, chunk_id: str, character_id: str, text_value: str
    ) -> bool:
        """Edit a chunk's text and RE-EMBED it (Phase 7).

        Vectors were write-only: the only fix for a bad sample was deleting the
        whole character. Editing must re-embed, or retrieval would rank the new
        text by the old vector. Returns False if the chunk isn't this character's.
        """
        embedding = _vector_literal(await self.embedder.aencode_one(text_value))
        async with self._session() as session:
            result = await session.execute(
                text("""
                    UPDATE voice_chunks
                    SET text = :text, word_count = :wc,
                        embedding = CAST(:embedding AS vector)
                    WHERE id = :id AND character_id = :character_id
                    """),
                {
                    "id": chunk_id,
                    "character_id": character_id,
                    "text": text_value,
                    "wc": len(text_value.split()),
                    "embedding": embedding,
                },
            )
            await session.commit()
        return (result.rowcount or 0) > 0

    async def delete_chunk(self, chunk_id: str, character_id: str) -> bool:
        """Remove one voice chunk (scoped to its character)."""
        async with self._session() as session:
            result = await session.execute(
                text(
                    "DELETE FROM voice_chunks "
                    "WHERE id = :id AND character_id = :character_id"
                ),
                {"id": chunk_id, "character_id": character_id},
            )
            await session.commit()
        return (result.rowcount or 0) > 0

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


def above_threshold(rows: list[dict], threshold: float) -> list[dict]:
    """The rows scoring >= `threshold`. Never strips a character's grounding to
    nothing: rows are scoped to the character and ordered by similarity, so if
    the threshold rejects them all, the closest ones are returned anyway rather
    than generating voice-blind."""
    above = [r for r in rows if r["score"] is None or r["score"] >= threshold]
    return above or rows


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
