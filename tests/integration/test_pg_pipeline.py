"""Postgres-only integration tests — coverage sqlite structurally can't give.

Runs the real Alembic migration chain against a pgvector Postgres, then
exercises the two paths that pass on sqlite but failed on Postgres:
  * pgvector index → retrieve round-trip
  * manuscript processing, which must COMMIT characters before indexing their
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


async def test_migrations_built_vector_and_manuscript_columns(pg):
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
        ms = (
            (
                await s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='manuscripts'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "content_text" in ms  # migration 0003 applied


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


async def test_process_manuscript_commits_before_indexing(pg, monkeypatch):
    """The FK regression: this raised on Postgres before the commit-first fix."""
    from app.core.orm_models import Manuscript, User
    from app.core.security import get_password_hash
    from app.rag.store import get_chunk_store
    import app.parsing.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.char_extractor,
        "extract_characters",
        lambda body, user_id=None: _val(["Mina"]),
    )
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
        ms = Manuscript(
            user_id=u.id, title="M", content_hash=uuid.uuid4().hex, content_text="body"
        )
        s.add(ms)
        await s.commit()
        await s.refresh(ms)
        uid, mid = u.id, ms.id

    await pipeline.process_manuscript(mid, uid, text="body")

    async with pg() as s:
        assert (
            await s.execute(
                text("SELECT status FROM manuscripts WHERE id=:i"), {"i": mid}
            )
        ).scalar() == "completed"
        assert (
            await s.execute(
                text("SELECT count(*) FROM voice_chunks WHERE user_id=:u"), {"u": uid}
            )
        ).scalar() == 2
        cid = (
            await s.execute(
                text("SELECT id FROM characters WHERE manuscript_id=:m"), {"m": mid}
            )
        ).scalar()

    hits = await get_chunk_store().retrieve_similar(
        character_id=str(cid), query="creatures of the night", k=2, user_id=str(uid)
    )
    assert isinstance(hits, list) and len(hits) >= 1
