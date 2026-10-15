"""SPEC-17 (B2) on real Postgres + pgvector: the e2e SOURCE, indexed by the
voice job, gives Milo Voss no dialogue chunk and Zara Okafor only her own line;
`reindex-voice` replaces chunks written by the old attributor.

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

# Copied from scripts/e2e_browser.py (SOURCE).
SOURCE = """Milo Voss opened his eyes in the intake atrium.

"You've been retained," the caseworker said. Milo Voss nodded slowly.

Zara Okafor, at the next desk, did not look up. "Don't thank her," Zara Okafor said.

Milo Voss read the appendices. Zara Okafor watched him read them.

Edric Thane signed the memo. Edric Thane had signed ten thousand memos.
"""
NAMES = ("Milo Voss", "Zara Okafor", "Edric Thane")


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


async def _book_with_source(pg):
    from app.core.orm_models import Book, Character, Source, User, source_characters
    from app.core.security import get_password_hash

    async with pg() as s:
        u = User(
            email=f"b2-{uuid.uuid4()}@ex.com",
            hashed_password=get_password_hash("password123"),
            full_name="b2",
        )
        s.add(u)
        await s.commit()
        book = Book(user_id=u.id, title="Bored to Undeath")
        s.add(book)
        await s.commit()
        src = Source(
            user_id=u.id,
            book_id=book.id,
            kind="paste",
            title="Pasted",
            content_hash=uuid.uuid4().hex,
            content_text=SOURCE,
        )
        s.add(src)
        await s.commit()
        cast = {
            # Aliases as the stub extraction gives them: the alias pass (SPEC-09)
            # is where the real journey leaked the caseworker's line to Milo.
            n: Character(
                user_id=u.id,
                book_id=book.id,
                source_id=src.id,
                name=n,
                aliases=[n.split()[0]],
            )
            for n in NAMES
        }
        s.add_all(cast.values())
        await s.commit()
        for c in cast.values():
            await s.execute(
                source_characters.insert().values(source_id=src.id, character_id=c.id)
            )
        await s.commit()
        return u.id, book.id, src.id, {n: c.id for n, c in cast.items()}


async def _chunks(pg, character_id, chunk_type=None):
    sql = "SELECT text FROM voice_chunks WHERE character_id = :c"
    params = {"c": character_id}
    if chunk_type:
        sql += " AND chunk_type = :t"
        params["t"] = chunk_type
    async with pg() as s:
        return list((await s.execute(text(sql), params)).scalars().all())


async def test_e2e_source_voice_is_attributed_to_its_speaker(pg):
    import app.parsing.pipeline as pipeline

    uid, bid, sid, ids = await _book_with_source(pg)
    await pipeline.index_characters_voice(sid, bid, uid, [str(i) for i in ids.values()])

    assert await _chunks(pg, ids["Milo Voss"], "dialogue") == []
    for chunk in await _chunks(pg, ids["Milo Voss"]):
        assert "Don't thank her" not in chunk
        assert "You've been retained" not in chunk
    assert await _chunks(pg, ids["Zara Okafor"], "dialogue") == ["Don't thank her."]
    assert await _chunks(pg, ids["Edric Thane"], "dialogue") == []


async def test_reindex_voice_replaces_old_attributor_chunks(pg):
    from app.cli import _reindex_voice
    from app.rag.store import get_chunk_store

    uid, bid, sid, ids = await _book_with_source(pg)
    milo = ids["Milo Voss"]
    # What the old `Name [^"]*"…"` pattern stored for Milo, plus a manual
    # sample (no source) that a re-index must keep.
    store = get_chunk_store()
    await store.index_chunks(
        character_id=str(milo),
        character_name="Milo Voss",
        user_id=str(uid),
        book_id=str(bid),
        source_id=str(sid),
        chunks=[
            {"text": "Don't thank her,", "chunk_type": "dialogue"},
            {"text": "You've been retained,", "chunk_type": "dialogue"},
        ],
    )
    await store.index_chunks(
        character_id=str(milo),
        character_name="Milo Voss",
        user_id=str(uid),
        book_id=str(bid),
        chunks=[{"text": "I keep my own counsel.", "chunk_type": "dialogue"}],
    )

    assert await _reindex_voice(bid) == 1

    assert await _chunks(pg, milo, "dialogue") == ["I keep my own counsel."]
    assert await _chunks(pg, ids["Zara Okafor"], "dialogue") == ["Don't thank her."]
    # Idempotent: a second run leaves the same chunks.
    before = sorted(await _chunks(pg, milo))
    assert await _reindex_voice(bid) == 1
    assert sorted(await _chunks(pg, milo)) == before
