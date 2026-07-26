"""Postgres-only integration tests — coverage sqlite structurally can't give.

Runs the real Alembic baseline against a pgvector Postgres, then exercises the
two paths that pass on sqlite but failed on Postgres:
  * pgvector index → retrieve round-trip
  * source processing, which must COMMIT characters before indexing their
    voice chunks (voice_chunks→characters FK lives on a separate connection).

Skipped unless RUN_PG_TESTS is set (CI provides a pgvector service).
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUN_PG_TESTS"),
        reason="Postgres integration tests only run when RUN_PG_TESTS is set",
    ),
]


async def _val(value):
    return value


@pytest.fixture
async def pg(monkeypatch):
    """Migrated engine + a session factory the whole app is pointed at."""
    from alembic import command
    from alembic.config import Config
    from app.core.database import get_async_db_url
    import app.core.database as db_mod

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")  # idempotent after first

    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    # Point the app's single session-factory accessor at this engine — both the
    # pipeline (get_async_session) and the vector store resolve through it.
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)
    try:
        yield Session
    finally:
        await engine.dispose()


async def test_baseline_built_vector_and_source_columns(pg):
    async with pg() as s:
        assert (
            await s.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'"))
        ).scalar() == 1
        vc = (
            (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='voice_chunks'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "embedding" in vc
        assert "book_id" in vc  # voice chunks are book-rooted now
        # `manuscripts` is gone; the book-rooted `sources` table replaces it.
        src = (
            (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='sources'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "content_text" in src
        assert "book_id" in src
        assert (
            await s.execute(text("SELECT to_regclass('public.manuscripts')"))
        ).scalar() is None


async def test_jobs_table_schema(pg):
    """Migration 0004: jobs table exists with a JSONB payload."""
    async with pg() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name='jobs'"
                )
            )
        ).all()
        cols = dict(rows)
        assert cols["payload"] == "jsonb"
        assert "available_at" in cols and "locked_at" in cols


async def test_tenant_ownership_enforced_in_schema(pg):
    """Migration 0005: characters/scenes.user_id NOT NULL + budget-path index."""
    async with pg() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT table_name, is_nullable FROM information_schema.columns "
                    "WHERE column_name='user_id' "
                    "AND table_name IN ('characters', 'scenes')"
                )
            )
        ).all()
        assert dict(rows) == {"characters": "NO", "scenes": "NO"}
        assert (
            await s.execute(
                text(
                    "SELECT 1 FROM pg_indexes WHERE indexname='idx_api_usage_user_time'"
                )
            )
        ).scalar() == 1


async def test_position_unique_constraints_in_schema(pg):
    """Migration 0006: UNIQUE (book_id, position) / (chapter_id, position)."""
    async with pg() as s:
        names = (
            (
                await s.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conname IN "
                        "('uq_chapters_book_position', 'uq_scenes_chapter_position')"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sorted(names) == [
            "uq_chapters_book_position",
            "uq_scenes_chapter_position",
        ]


async def test_claim_one_skip_locked_across_sessions(pg):
    """Two concurrent claimers must get distinct jobs (FOR UPDATE SKIP LOCKED)."""
    from app.core.orm_models import User
    from app.core.security import get_password_hash
    from app.jobs import repository as jobs_repo

    async with pg() as s:
        u = User(
            email=f"pg-{uuid.uuid4()}@ex.com",
            hashed_password=get_password_hash("password123"),
            full_name="pg",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        j1 = await jobs_repo.enqueue(
            s, kind="test_kind", payload={"n": 1}, user_id=u.id
        )
        j2 = await jobs_repo.enqueue(
            s, kind="test_kind", payload={"n": 2}, user_id=u.id
        )
        await s.commit()
        ids = {j1.id, j2.id}

    async with pg() as s1, pg() as s2:
        # s1 claims and holds its row lock open; s2 must skip past it.
        c1 = await jobs_repo.claim_one(s1, worker_id="w1")
        c2 = await jobs_repo.claim_one(s2, worker_id="w2")
        assert c1 is not None and c2 is not None
        assert {c1.id, c2.id} == ids
        await s1.commit()
        await s2.commit()

    async with pg() as s:
        # cleanup so the test is rerunnable against a persistent DB
        await s.execute(text("DELETE FROM jobs WHERE kind='test_kind'"))
        await s.commit()


async def test_index_source_voices_indexes_committed_characters(pg, monkeypatch):
    """index_source_voices indexes an already-committed character's voice on real
    pgvector (the FK: voice_chunks -> characters, committed first)."""
    from app.core.orm_models import Book, Character, Source, User
    from app.core.security import get_password_hash
    from app.rag.store import get_chunk_store
    import app.parsing.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.char_extractor,
        "extract_character_content",
        lambda body, name: [
            {
                "chunk_type": "dialogue",
                "text": "The dead travel fast.",
                "source_location": "1",
            },
            {
                "chunk_type": "dialogue",
                "text": "Children of the night, what music they make.",
                "source_location": "2",
            },
        ],
    )
    monkeypatch.setattr(
        pipeline.char_extractor,
        "get_character_statistics",
        lambda chunks: {"dialogue_count": len(chunks)},
    )

    async with pg() as s:
        u = User(
            email=f"pg-{uuid.uuid4()}@ex.com",
            hashed_password=get_password_hash("password123"),
            full_name="pg",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        book = Book(user_id=u.id, title="M")
        s.add(book)
        await s.commit()
        await s.refresh(book)
        src = Source(
            user_id=u.id,
            book_id=book.id,
            title="M",
            content_hash=uuid.uuid4().hex,
            content_text="body",
        )
        s.add(src)
        await s.commit()
        await s.refresh(src)
        # a committed character with no voice yet (indexed_at IS NULL)
        ch = Character(user_id=u.id, book_id=book.id, source_id=src.id, name="Mina")
        s.add(ch)
        await s.commit()
        await s.refresh(ch)
        uid, bid, sid, cid = u.id, book.id, src.id, ch.id

    await pipeline.index_source_voices(sid, bid, uid)

    async with pg() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM voice_chunks WHERE user_id=:u"), {"u": uid}
            )
        ).scalar() == 2
        assert (
            await s.execute(
                text("SELECT indexed_at IS NOT NULL FROM characters WHERE id=:c"),
                {"c": cid},
            )
        ).scalar() is True

    hits = await get_chunk_store().retrieve_similar(
        character_id=str(cid), query="creatures of the night", k=2, user_id=str(uid)
    )
    assert isinstance(hits, list) and len(hits) >= 1


async def test_chunk_browser_edit_delete_roundtrip(pg):
    """Phase 7: list / re-embed-on-edit / delete voice chunks against real pgvector."""
    import uuid as _uuid

    from app.core.orm_models import Book, Character, User
    from app.core.security import get_password_hash
    from app.rag.store import get_chunk_store

    async with pg() as s:
        u = User(
            email=f"pg-{_uuid.uuid4()}@ex.com",
            hashed_password=get_password_hash("password123"),
            full_name="pg",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        book = Book(user_id=u.id, title="B")
        s.add(book)
        await s.commit()
        await s.refresh(book)
        ch = Character(user_id=u.id, book_id=book.id, name="Mina")
        s.add(ch)
        await s.commit()
        await s.refresh(ch)
        cid, bid = str(ch.id), str(book.id)

    store = get_chunk_store()
    await store.index_chunks(
        character_id=cid,
        character_name="Mina",
        user_id=str(u.id),
        book_id=bid,
        chunks=[
            {"text": "The dead travel fast.", "chunk_type": "dialogue"},
            {"text": "Children of the night.", "chunk_type": "dialogue"},
        ],
    )
    chunks = await store.list_chunks(cid)
    assert len(chunks) == 2

    target = chunks[0]["id"]
    assert await store.update_chunk(target, cid, "Listen to them, the music they make.")
    listed = {c["id"]: c["text"] for c in await store.list_chunks(cid)}
    assert listed[target] == "Listen to them, the music they make."

    assert await store.delete_chunk(target, cid)
    assert len(await store.list_chunks(cid)) == 1
    # wrong-character scoping: cannot delete another id
    assert await store.delete_chunk(target, cid) is False
