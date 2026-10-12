"""SPEC-13 on real Postgres + pgvector: a trashed character keeps its voice,
and purge removes chunks and row in one transaction.

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

QUERY = "Children of the night, what music they make."
LINES = [
    "Listen to them, the children of the night.",
    "The dead travel fast.",
    "Enter freely and of your own will.",
]


@pytest.fixture
async def pg(monkeypatch):
    from alembic import command
    from alembic.config import Config

    import app.core.database as db_mod
    from app.core.database import get_async_db_url

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)
    try:
        yield Session
    finally:
        await engine.dispose()


async def _voiced_character(pg, name="Dracula"):
    from app.core.orm_models import Book, Character, User
    from app.rag.store import get_chunk_store

    async with pg() as s:
        u = User(email=f"trash-{uuid.uuid4()}@ex.com", hashed_password="x")
        s.add(u)
        await s.commit()
        book = Book(user_id=u.id, title="B")
        s.add(book)
        await s.commit()
        ch = Character(user_id=u.id, book_id=book.id, name=name, role="antagonist")
        s.add(ch)
        await s.commit()
        ids = (u.id, book.id, ch.id)
    await get_chunk_store().index_chunks(
        character_id=str(ids[2]),
        character_name=name,
        user_id=str(ids[0]),
        book_id=str(ids[1]),
        chunks=[{"text": t, "chunk_type": "dialogue"} for t in LINES],
    )
    return ids


async def _chunk_count(pg, character_id) -> int:
    async with pg() as s:
        return (
            await s.execute(
                text("SELECT COUNT(*) FROM voice_chunks WHERE character_id = :c"),
                {"c": character_id},
            )
        ).scalar_one()


async def test_acceptance_2_restore_brings_back_bible_and_voice(pg):
    """Deleting and restoring a character brings back its bible fields and its
    voice chunks, and retrieval for it returns the same results as before."""
    from app.core.orm_models import Character
    from app.rag.store import get_chunk_store
    from app.versioning import trash

    uid, bid, cid = await _voiced_character(pg)
    store = get_chunk_store()
    before = await store.retrieve_similar(str(cid), QUERY, k=3, score_threshold=-1.0)
    assert len(before) == 3

    async with pg() as s:
        row = await s.get(Character, cid)
        await trash.soft_delete(s, "character", row, created_by=uid)
        await s.commit()

    # trashed: the chunks are kept, but nothing retrieves them
    assert await _chunk_count(pg, cid) == 3
    assert (
        await store.retrieve_similar(str(cid), QUERY, k=3, score_threshold=-1.0) == []
    )
    async with pg() as s:
        listed = await trash.list_trash(s, bid)
    assert listed[0]["restores"] == {"voice_samples": 3}
    assert listed[0]["summary"] == "the character bible and 3 voice samples"

    async with pg() as s:
        row = await s.get(Character, cid)
        await trash.restore(s, "character", row, created_by=uid)
        await s.commit()
    async with pg() as s:
        row = await s.get(Character, cid)
        assert row.deleted_at is None
        assert (row.name, row.role) == ("Dracula", "antagonist")

    after = await store.retrieve_similar(str(cid), QUERY, k=3, score_threshold=-1.0)
    assert [(r["id"], r["text"]) for r in after] == [
        (r["id"], r["text"]) for r in before
    ]
    assert [r["score"] for r in after] == pytest.approx([r["score"] for r in before])


async def test_acceptance_5_purge_removes_chunks_and_row_in_one_transaction(pg):
    from app.core.orm_models import Character
    from app.versioning import trash

    uid, bid, cid = await _voiced_character(pg, "Mina")
    async with pg() as s:
        row = await s.get(Character, cid)
        await trash.soft_delete(s, "character", row, created_by=uid)
        await s.commit()

    # Purge, then look from ANOTHER connection before committing: nothing has
    # happened yet — the chunk delete did not commit on its own.
    async with pg() as s:
        row = await s.get(Character, cid)
        await trash.purge(s, "character", row)
        assert await _chunk_count(pg, cid) == 3
        await s.rollback()
    assert await _chunk_count(pg, cid) == 3
    async with pg() as s:
        assert await s.get(Character, cid) is not None

    async with pg() as s:
        row = await s.get(Character, cid)
        await trash.purge(s, "character", row)
        await s.commit()
    assert await _chunk_count(pg, cid) == 0
    async with pg() as s:
        assert await s.get(Character, cid) is None


async def test_partial_unique_indexes_replace_the_constraints(pg):
    names = (
        "uq_characters_book_name",
        "uq_canon_entries_book_name",
        "uq_sources_book_content",
        "uq_chapters_book_position",
        "uq_scenes_chapter_position",
    )
    async with pg() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE indexname = ANY(:n)"
                ),
                {"n": list(names)},
            )
        ).all()
        constraints = (
            await s.execute(
                text("SELECT conname FROM pg_constraint WHERE conname = ANY(:n)"),
                {"n": list(names)},
            )
        ).all()
    assert sorted(r.indexname for r in rows) == sorted(names)
    assert all(
        "UNIQUE" in r.indexdef and "deleted_at IS NULL" in r.indexdef for r in rows
    )
    assert constraints == []


async def test_downgrade_restores_full_constraints_then_upgrade_again(pg):
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")

    async def has_column() -> bool:
        async with pg() as s:
            return bool(
                (
                    await s.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns WHERE "
                            "table_name = 'characters' AND column_name = 'deleted_at'"
                        )
                    )
                ).first()
            )

    assert await has_column()
    await asyncio.to_thread(command.downgrade, cfg, "spec11_drop_character_chunks")
    try:
        assert not await has_column()
        async with pg() as s:
            assert (
                await s.execute(
                    text(
                        "SELECT 1 FROM pg_constraint "
                        "WHERE conname = 'uq_characters_book_name'"
                    )
                )
            ).first()
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await has_column()
