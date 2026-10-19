"""Unit tests for the pgvector-backed chunk store."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.characters.context as ctx
from app.rag.embeddings import cosine_similarity
from app.rag.store import ChunkStore, _vector_literal
from tests.unit.factories import make_scope

STARVE = json.loads(
    (Path(__file__).parents[1] / "fixtures/retrieval_starve.json").read_text()
)


class FakeSession:
    """Records executed statements; returns canned rows for SELECTs."""

    def __init__(self, rows=None, fail=False, rowcount=1):
        self.rows = rows or []
        self.fail = fail
        self.rowcount = rowcount
        self.executed: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, statement, params=None):
        if self.fail:
            raise RuntimeError("db down")
        self.executed.append((str(statement), params or {}))
        result = MagicMock()
        rows = self.rows[: (params or {}).get("k")]  # LIMIT :k, as Postgres
        result.mappings.return_value.all.return_value = rows
        result.first.return_value = self.rows[0] if self.rows else None
        result.rowcount = self.rowcount
        return result

    async def commit(self):
        self.committed = True


def make_store(rows=None, fail=False, rowcount=1) -> tuple[ChunkStore, FakeSession]:
    session = FakeSession(rows=rows, fail=fail, rowcount=rowcount)

    @asynccontextmanager
    async def factory():
        yield session

    with patch("app.rag.store.get_embedder") as get_embedder:
        embedder = MagicMock()
        embedder.dimension = 384
        embedder.aencode = AsyncMock(
            side_effect=lambda texts: [[0.1] * 384 for _ in texts]
        )
        embedder.aencode_one = AsyncMock(return_value=[0.1] * 384)
        get_embedder.return_value = embedder
        store = ChunkStore(session_factory=factory)
    return store, session


@pytest.mark.unit
class TestVectorLiteral:
    def test_format(self):
        literal = _vector_literal([0.5, -1.0, 0.25])
        assert literal.startswith("[") and literal.endswith("]")
        assert len(literal.split(",")) == 3


@pytest.mark.unit
class TestChunkStore:
    @pytest.mark.asyncio
    async def test_index_chunks_inserts_with_payload(self):
        store, session = make_store()
        chunks = [
            {"text": "Hello there", "chunk_type": "dialogue", "source_location": "p1"},
            {"text": "She ran off", "chunk_type": "action", "source_location": "p2"},
        ]
        count = await store.index_chunks(
            character_id="char-1",
            character_name="Alice",
            user_id="user-1",
            book_id="book-1",
            chunks=chunks,
        )
        assert count == 2
        assert session.committed
        inserts = [e for e in session.executed if "INSERT INTO voice_chunks" in e[0]]
        assert len(inserts) == 2
        assert inserts[0][1]["character_id"] == "char-1"
        assert inserts[0][1]["user_id"] == "user-1"
        assert inserts[0][1]["book_id"] == "book-1"
        assert inserts[1][1]["chunk_type"] == "action"
        assert inserts[0][1]["embedding"].startswith("[")

    @pytest.mark.asyncio
    async def test_index_empty_is_zero(self):
        store, session = make_store()
        assert (
            await store.index_chunks(
                character_id="c",
                character_name="n",
                user_id="u",
                book_id="b",
                chunks=[],
            )
            == 0
        )
        assert session.executed == []

    @pytest.mark.asyncio
    async def test_retrieve_filters_by_character_and_type(self):
        rows = [
            {
                "text": "sample",
                "chunk_type": "dialogue",
                "source_location": "p1",
                "word_count": 1,
                "score": 0.9,
            }
        ]
        store, session = make_store(rows=rows)
        results = await store.retrieve_similar(
            character_id="char-1", query="query", chunk_type="dialogue"
        )
        assert results[0]["text"] == "sample"
        assert results[0]["score"] == pytest.approx(0.9)
        sql, params = session.executed[0]
        assert "character_id = :character_id" in sql
        assert "chunk_type = :chunk_type" in sql
        assert params["character_id"] == "char-1"

    @pytest.mark.asyncio
    async def test_retrieve_applies_score_threshold(self):
        rows = [
            {
                "text": "good",
                "chunk_type": "dialogue",
                "source_location": "",
                "word_count": 1,
                "score": 0.9,
            },
            {
                "text": "weak",
                "chunk_type": "dialogue",
                "source_location": "",
                "word_count": 1,
                "score": 0.1,
            },
        ]
        store, _ = make_store(rows=rows)
        results = await store.retrieve_similar(
            character_id="c", query="q", score_threshold=0.5
        )
        assert [r["text"] for r in results] == ["good"]

    @pytest.mark.asyncio
    async def test_retrieve_failure_returns_empty(self):
        store, _ = make_store(fail=True)
        assert await store.retrieve_similar(character_id="c", query="q") == []

    @pytest.mark.asyncio
    async def test_statistics_aggregates_types(self):
        rows = [
            {"chunk_type": "dialogue", "n": 3, "words": 30},
            {"chunk_type": "action", "n": 1, "words": 5},
        ]
        store, _ = make_store(rows=rows)
        stats = await store.character_statistics("char-1")
        assert stats["total_chunks"] == 4
        assert stats["type_distribution"] == {"dialogue": 3, "action": 1}
        assert stats["total_words"] == 35

    @pytest.mark.asyncio
    async def test_delete_character(self):
        store, session = make_store()
        await store.delete_character("char-1")
        sql, params = session.executed[0]
        assert "DELETE FROM voice_chunks" in sql
        assert params["character_id"] == "char-1"
        assert session.committed

    @pytest.mark.asyncio
    async def test_healthy_checks_extension(self):
        store, session = make_store(rows=[{"?column?": 1}])
        assert await store.healthy() is True
        assert "pg_extension" in session.executed[0][0]

    @pytest.mark.asyncio
    async def test_unhealthy_on_db_error(self):
        store, _ = make_store(fail=True)
        assert await store.healthy() is False


@pytest.mark.unit
class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


async def _voice_block(monkeypatch, extra=0):
    """SPEC-83: the voice block (k=5) and its grounding reasons over the fixture,
    plus `extra` ineligible rows below rank 51."""
    row = {"chunk_type": "dialogue", "source_location": "", "word_count": 1}
    rows = [row | dict(r, id=r["rank"]) for r in STARVE["rows"]]
    rows += [dict(rows[0], id=-i, score=0.5) for i in range(extra)]
    store, session = make_store(rows)
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    who, out = SimpleNamespace(id="m", name="Milo", user_id=None, book_id=None), []
    kw = dict(scope=make_scope(), threshold=0.2, chunk_types=["dialogue"])
    words, _ = await ctx.character_samples(who, "q", 5, withheld=out, **kw)
    reasons = [g.get("reason") for g in out]
    return [w["text"] for w in words], reasons, session.executed[0][0]


@pytest.mark.unit
async def test_the_eligible_row_at_rank_51_fills_the_starved_block(monkeypatch):  # (F)
    shown, reasons, sql = await _voice_block(monkeypatch)
    assert (shown, reasons) == ([STARVE["rows"][50]["text"]], [])
    assert "<=> CAST(:q AS vector), id LIMIT :k" in sql


@pytest.mark.unit
async def test_a_short_block_at_the_bound_records_why(monkeypatch):  # (F)
    shown, reasons, _ = await _voice_block(monkeypatch, extra=149)
    assert (len(shown), reasons) == (1, [ctx.RETRIEVAL_EXHAUSTED_REASON])
