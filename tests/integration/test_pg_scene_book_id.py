"""SPEC-04 on real Postgres: the scenes.book_id migration.

* Backfill — on a database at the revision before SPEC-04, scenes in chapters
  take the chapter's book, source-only scenes take the source's book, and a scene
  that reaches no book is deleted (with its revisions).
* Shape parity — a fresh database migrated to head has exactly the `scenes`
  shape of an upgraded one (columns, constraints, indexes).
* The cascade — deleting a book deletes its scenes, beats and revisions.

Skipped unless RUN_PG_TESTS is set (CI provides a pgvector service).
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUN_PG_TESTS"),
        reason="Postgres integration tests only run when RUN_PG_TESTS is set",
    ),
]

REVISION = "spec04_scene_book_id"


def _alembic():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    # Resolved, not hard-coded: the coordinator re-chains parallel migrations.
    before = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
    return cfg, before


async def _upgrade(cfg, target="head"):
    from alembic import command

    await asyncio.to_thread(command.upgrade, cfg, target)


async def _downgrade(cfg, target):
    from alembic import command

    await asyncio.to_thread(command.downgrade, cfg, target)


@pytest.fixture
async def engine():
    from app.core.database import get_async_db_url

    cfg, _ = _alembic()
    await _upgrade(cfg)
    eng = create_async_engine(get_async_db_url())
    try:
        yield eng
    finally:
        await eng.dispose()


async def _scalar(conn, sql, **params):
    return (await conn.execute(text(sql), params)).scalar()


async def _scenes_shape(eng) -> dict:
    async with eng.connect() as c:
        columns = (
            await c.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='scenes' "
                    "ORDER BY column_name"
                )
            )
        ).all()
        constraints = (
            await c.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='scenes'::regclass ORDER BY conname"
                )
            )
        ).all()
        indexes = (
            await c.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname='public' AND tablename='scenes' "
                    "ORDER BY indexname"
                )
            )
        ).all()
    return {
        "columns": [tuple(r) for r in columns],
        "constraints": [tuple(r) for r in constraints],
        "indexes": [tuple(r) for r in indexes],
    }


async def _seed_legacy(eng) -> dict:
    """Pre-SPEC-04 rows: no book_id column exists yet, so raw SQL."""
    ids = {k: uuid.uuid4() for k in ("u", "a", "b", "ch", "src", "s1", "s2", "s3")}
    ids["s4"] = uuid.uuid4()
    async with eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO users (id, email, hashed_password) "
                "VALUES (:u, :email, 'x')"
            ),
            {"u": ids["u"], "email": f"spec04-{ids['u']}@ex.com"},
        )
        for book in ("a", "b"):
            await c.execute(
                text(
                    "INSERT INTO books (id, user_id, title, status) VALUES (:id, :u, 'B', 'drafting')"
                ),
                {"id": ids[book], "u": ids["u"]},
            )
        await c.execute(
            text(
                "INSERT INTO chapters (id, book_id, title, position, status) "
                "VALUES (:ch, :a, 'C', 0, 'drafting')"
            ),
            {"ch": ids["ch"], "a": ids["a"]},
        )
        await c.execute(
            text(
                "INSERT INTO sources (id, book_id, user_id, title, kind, status) "
                "VALUES (:src, :b, :u, 'S', 'paste', 'completed')"
            ),
            {"src": ids["src"], "b": ids["b"], "u": ids["u"]},
        )
        # s1: in a chapter of A. s2: source-only, B. s3: chapter (A) wins over
        # source (B). s4: reaches no book — an orphan the migration deletes.
        rows = [
            ("s1", ids["ch"], None, 0),
            ("s2", None, ids["src"], 0),
            ("s3", ids["ch"], ids["src"], 1),
            ("s4", None, None, 0),
        ]
        for key, ch, src, pos in rows:
            await c.execute(
                text(
                    "INSERT INTO scenes (id, user_id, chapter_id, source_id, position)"
                    " VALUES (:id, :u, :ch, :src, :pos)"
                ),
                {"id": ids[key], "u": ids["u"], "ch": ch, "src": src, "pos": pos},
            )
        await c.execute(
            text(
                "INSERT INTO scene_revisions (id, scene_id, content, source) "
                "VALUES (:id, :s4, 'orphan prose', 'edited')"
            ),
            {"id": uuid.uuid4(), "s4": ids["s4"]},
        )
    return ids


async def test_backfill_roots_every_scene_in_its_book(engine):
    cfg, before = _alembic()
    await _downgrade(cfg, before)
    async with engine.connect() as c:
        assert not await _scalar(
            c,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='scenes' AND column_name='book_id'",
        )
    ids = await _seed_legacy(engine)
    await _upgrade(cfg)

    async with engine.connect() as c:
        got = dict(
            (
                await c.execute(
                    text("SELECT id, book_id FROM scenes WHERE id = ANY(:ids)"),
                    {"ids": [ids[k] for k in ("s1", "s2", "s3", "s4")]},
                )
            ).all()
        )
        assert got == {ids["s1"]: ids["a"], ids["s2"]: ids["b"], ids["s3"]: ids["a"]}
        # the orphan went, and its revision with it
        assert (
            await _scalar(
                c,
                "SELECT count(*) FROM scene_revisions WHERE scene_id = :s",
                s=ids["s4"],
            )
            == 0
        )
        assert (
            await _scalar(c, "SELECT count(*) FROM scenes WHERE book_id IS NULL") == 0
        )


async def test_fresh_and_upgraded_scenes_have_the_same_shape(engine, monkeypatch):
    from app.core import config
    from app.core.database import get_async_db_url

    # Upgraded: walk this database back over SPEC-04 (with data) and forward.
    cfg, before = _alembic()
    await _downgrade(cfg, before)
    await _seed_legacy(engine)
    await _upgrade(cfg)
    upgraded = await _scenes_shape(engine)

    # Fresh: a brand-new database migrated from nothing.
    url = make_url(get_async_db_url())
    fresh_name = f"{url.database}_fresh_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as c:
            await c.execute(text(f'CREATE DATABASE "{fresh_name}"'))
        fresh_url = url.set(database=fresh_name).render_as_string(hide_password=False)
        monkeypatch.setattr(config.settings, "DATABASE_URL", fresh_url)
        await _upgrade(cfg)
        fresh_engine = create_async_engine(fresh_url)
        try:
            fresh = await _scenes_shape(fresh_engine)
        finally:
            await fresh_engine.dispose()
    finally:
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}"'))
        await admin.dispose()

    assert fresh == upgraded
    columns = {name: nullable for name, _, nullable, _ in fresh["columns"]}
    assert columns["book_id"] == "NO"
    constraints = dict(fresh["constraints"])
    assert constraints["scenes_book_id_fkey"] == (
        "FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE"
    )
    assert "idx_scenes_book_id" in dict(fresh["indexes"])


async def test_deleting_a_book_deletes_its_scenes_beats_and_revisions(engine):
    ids = {k: uuid.uuid4() for k in ("u", "book", "ch", "scene")}
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:u, :e, 'x')"),
            {"u": ids["u"], "e": f"spec04-{ids['u']}@ex.com"},
        )
        await c.execute(
            text(
                "INSERT INTO books (id, user_id, title, status) VALUES (:b, :u, 'B', 'drafting')"
            ),
            {"b": ids["book"], "u": ids["u"]},
        )
        await c.execute(
            text(
                "INSERT INTO chapters (id, book_id, title, position, status) "
                "VALUES (:ch, :b, 'C', 0, 'drafting')"
            ),
            {"ch": ids["ch"], "b": ids["book"]},
        )
        await c.execute(
            text(
                "INSERT INTO scenes (id, user_id, book_id, chapter_id, position) "
                "VALUES (:s, :u, :b, :ch, 0)"
            ),
            {"s": ids["scene"], "u": ids["u"], "b": ids["book"], "ch": ids["ch"]},
        )
        await c.execute(
            text(
                "INSERT INTO scene_beats (id, scene_id, beat_number) VALUES (:id, :s, 1)"
            ),
            {"id": uuid.uuid4(), "s": ids["scene"]},
        )
        await c.execute(
            text(
                "INSERT INTO scene_revisions (id, scene_id, content, source) "
                "VALUES (:id, :s, 'prose', 'edited')"
            ),
            {"id": uuid.uuid4(), "s": ids["scene"]},
        )
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM books WHERE id = :b"), {"b": ids["book"]})
    async with engine.connect() as c:
        for table, col in (
            ("scenes", "id"),
            ("scene_beats", "scene_id"),
            ("scene_revisions", "scene_id"),
        ):
            assert (
                await _scalar(
                    c, f"SELECT count(*) FROM {table} WHERE {col} = :s", s=ids["scene"]
                )
                == 0
            ), table
