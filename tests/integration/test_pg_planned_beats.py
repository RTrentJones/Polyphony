"""SPEC-23 on real Postgres: the `w2_planned_beats` migration.

* `chapters.planned_beats` is JSONB after upgrade, gone after a downgrade to the
  revision before it.
* The backfill parses the summary's promote-format "Planned beats:" lines only:
  the author's edited text is kept, the outline is never read, a summary that
  merely mentions the phrase is skipped, a block may open the summary, text
  after the block stays, and a beat's continuation line stays in that beat.
* Each converted chapter's pre-SPEC-23 scenes are linked to their beats once
  (`scene_request.planned_beat_id`), so generate-planned skips them (viii).
* Promoted titles lose a leading "Chapter N:", unless that empties them.
* upgrade -> downgrade -> upgrade leaves the same summaries and beats.
* The migration's inline parser equals `planned_beat.split_legacy_summary`.

Skipped unless RUN_PG_TESTS is set (CI provides a pgvector service).
"""

import asyncio
import importlib.util
import logging
import os
import uuid
from pathlib import Path

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

REVISION = "w2_planned_beats"
_MIGRATION = Path(__file__).parents[2] / "alembic/versions/0019_w2_planned_beats.py"


def _alembic():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    before = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
    return cfg, before


async def _alembic_cmd(name, cfg, target):
    from alembic import command

    await asyncio.to_thread(getattr(command, name), cfg, target)


async def _column_type(engine):
    async with engine.connect() as c:
        return (
            await c.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='chapters' "
                    "AND column_name='planned_beats'"
                )
            )
        ).scalar()


async def _chapters(engine, ids):
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT id, title, summary, planned_beats FROM chapters "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
        ).fetchall()
    return {r[0]: r for r in rows}


def _shape(beats):
    return [(b["title"], b["summary"]) for b in beats or []]


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _migration_module():
    spec = importlib.util.spec_from_file_location("m0019", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_THIRTEEN = "Ch.\n\nPlanned beats:\n" + "\n".join(f"- B{i}: s{i}" for i in range(13))
_FIXTURES = [
    "Milo arrives\n\nPlanned beats:\n- Badge photo: he is dead\n- Lunch:",
    "Planned beats:\n- Storm: the moor floods",
    "Head\n\nPlanned beats:\n- A: one\n  more of one\n- B: two\n\nAfter the block.",
    "Planned beats: TBD. Then the storm.",
    "Notes\n\nPlanned beats:\nTBD",
    "x\n\nPlanned beats:\n- :\n- : orphan summary\n- Bare\n- " + "T" * 600 + ": s",
    "No marker at all.",
    _THIRTEEN,
    "x\n\nPlanned beats:\n- A: a\n- : the author's orphan line",
]
# NB-2: these lose text if converted, so 0019 and restore leave them as text.
_LOSSY = {_THIRTEEN, _FIXTURES[5], _FIXTURES[-1]}


def test_the_inline_parser_matches_the_app():
    """(vii) Parity: the migration's copies equal the app's functions."""
    from app.api.versioning import keeps_every_word
    from app.planning.planned_beat import (
        beat_from_line,
        normalize_planned_beats,
        split_legacy_summary,
    )

    m = _migration_module()
    for summary in _FIXTURES:
        app_split = split_legacy_summary(summary)
        ours = m._split(summary)
        if app_split is None:
            assert ours is None, summary
            continue
        assert ours == (app_split.text, [(x.head, x.text) for x in app_split.lines])
        app_beats = normalize_planned_beats(
            [beat_from_line(x.text) for x in app_split.lines]
        )
        mig_beats = [b for b in (m._beat(line) for _, line in ours[1]) if b][:12]
        keeps = keeps_every_word(summary, app_split.text, app_beats)
        assert keeps == m._keeps_every_word(summary, ours[0], mig_beats)
        assert keeps == (summary not in _LOSSY), summary
        assert [{**b, "node_id": ""} for b in mig_beats] == [
            {**b, "node_id": ""} for b in app_beats
        ]
        assert all(uuid.UUID(b["node_id"]).version == 4 for b in mig_beats)


async def _insert(conn, table, **row):
    cols = ", ".join(row)
    await conn.execute(
        text(
            f"INSERT INTO {table} ({cols}) VALUES ({', '.join(':' + c for c in row)})"
        ),
        row,
    )


async def test_migration_converts_links_strips_titles_and_round_trips(monkeypatch):
    import logging.config

    import httpx

    import app.core.database as db_mod
    from app.core import estimates
    from app.core.database import get_async_db_url
    from app.core.orm_models import Book, BookPlan, Character, Job, User
    from app.core.security import create_access_token
    from app.main import app

    # env.py's fileConfig would drop this test's handler from the logger.
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)
    log = _Lines()
    migration_logger = logging.getLogger("alembic.runtime.migration")
    migration_logger.addHandler(log)
    monkeypatch.setattr(migration_logger, "level", logging.INFO)

    cfg, before = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)

    async def _none():
        return None

    monkeypatch.setattr(estimates, "requests_left_today", _none)
    node_id = uuid.uuid4()
    try:
        assert await _column_type(engine) == "jsonb"
        async with Session() as s:
            user = User(email=f"beats-{uuid.uuid4()}@ex.com", hashed_password="x")
            s.add(user)
            await s.flush()
            book = Book(user_id=user.id, title="B", synopsis="Milo.")
            s.add(book)
            await s.flush()
            milo = Character(book_id=book.id, user_id=user.id, name="Milo Voss")
            s.add(milo)
            # The outline's child no longer matches the author's edited line.
            child = {"node_id": str(uuid.uuid4()), "title": "Badge photo"}
            s.add(
                BookPlan(
                    book_id=book.id,
                    kind="outline",
                    content=[
                        {
                            "node_id": str(node_id),
                            "title": "Chapter 2: Milo Voss",
                            "children": [{**child, "summary": "the outline's"}],
                        }
                    ],
                )
            )
            await s.commit()
            user_id, book_id, milo_id = user.id, book.id, str(milo.id)

        await _alembic_cmd("downgrade", cfg, before)
        assert await _column_type(engine) is None

        # Chapters as the pre-SPEC-23 promote (or an author) wrote them.
        edited, at_start, tail, stray, loose, wrapped, lossy = (
            uuid.uuid4() for _ in "1234567"
        )
        rows = [
            # (i) the author edited the promoted line
            (
                edited,
                "Chapter 2: Milo Voss",
                "Milo arrives\n\nPlanned beats:\n- Badge photo: he is dead\n- Lunch:",
                node_id,
            ),
            # (iv) an empty node summary put the block at offset 0
            (at_start, "Chapter 4:", "Planned beats:\n- Memo: a decree", uuid.uuid4()),
            # (v) the author typed below the block
            (tail, "T", "Head\n\nPlanned beats:\n- A: one\n- B: two\n\nAfter.", None),
            # (iii) a hand-written mention, not a block
            (stray, "Stray", "Planned beats: TBD. Then the storm.", None),
            (
                loose,
                "Chapter 3: Hand-made",  # no plan_node_id: title kept
                "Loose\n\nPlanned beats:\n- Part 1: Storm: floods\n- Bare",
                None,
            ),
            # (ix) a child summary with a newline continues its beat
            (
                wrapped,
                "Wrapped",
                "W\n\nPlanned beats:\n- Storm: the moor\nfloods at night\n- Shelter: barn",
                None,
            ),
            # NB-2: 13 beat lines and a "- : x" line would lose text: left as is
            (lossy, "Lossy", _THIRTEEN + "\n- : the orphan line", None),
        ]
        async with engine.begin() as c:
            for pos, (cid, title, summary, plan_node) in enumerate(rows):
                await _insert(
                    c,
                    "chapters",
                    id=cid,
                    book_id=book_id,
                    position=pos,
                    title=title,
                    summary=summary,
                    status="drafting",
                    plan_node_id=plan_node,
                )
            await c.execute(
                text("UPDATE chapters SET character_ids = CAST(:c AS json)"),
                {"c": f'["{milo_id}"]'},
            )
            # Pre-SPEC-23 scenes, written from the "- " lines (no beat id).
            scenes = [
                (edited, 0, "Badge photo: he is dead", "completed"),
                (edited, 1, "Lunch:", "failed"),  # failed: never linked
                (wrapped, 0, "Storm: the moor", "completed"),
                (tail, 0, "Something else", "completed"),
            ]
            for chapter_id, pos, description, status in scenes:
                await _insert(
                    c,
                    "scenes",
                    id=uuid.uuid4(),
                    user_id=user_id,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    position=pos,
                    scene_description=description,
                    status=status,
                )

        log.lines.clear()
        await _alembic_cmd("upgrade", cfg, "head")
        assert await _column_type(engine) == "jsonb"
        (line,) = [x for x in log.lines if x.startswith("0019 planned beats: ")]
        # At least this test's rows (other tests' chapters may share the DB).
        counts = dict(kv.split("=") for kv in line.split(": ")[1].split())
        assert int(counts["converted"]) >= 5 and int(counts["skipped"]) >= 2
        assert int(counts["scenes_linked"]) >= 2 and int(counts["beats"]) >= 9
        ids = [r[0] for r in rows]
        got = await _chapters(engine, ids)
        expected = {
            edited: (
                "Milo Voss",
                "Milo arrives",
                [("Badge photo", "he is dead"), ("Lunch", "")],
            ),
            at_start: ("Chapter 4:", "", [("Memo", "a decree")]),  # would empty it
            tail: ("T", "Head\n\nAfter.", [("A", "one"), ("B", "two")]),
            stray: ("Stray", "Planned beats: TBD. Then the storm.", []),
            loose: (
                "Chapter 3: Hand-made",
                "Loose",
                [("Part 1", "Storm: floods"), ("Bare", "")],
            ),
            wrapped: (
                "Wrapped",
                "W",
                [("Storm", "the moor floods at night"), ("Shelter", "barn")],
            ),
            lossy: ("Lossy", _THIRTEEN + "\n- : the orphan line", []),
        }
        for cid, (title, summary, beats) in expected.items():
            assert (got[cid][1], got[cid][2], _shape(got[cid][3])) == (
                title,
                summary,
                beats,
            )
        assert got[stray][3] is None and got[lossy][3] is None
        beats = got[edited][3]
        assert all(uuid.UUID(b["node_id"]).version == 4 for b in beats)
        assert beats[0]["character_ids"] == [] and beats[0]["tone"] == ""

        # The scenes: linked once, by occurrence, to the beat of their line.
        async with engine.connect() as c:
            linked = {
                (r[0], r[1]): r[2]
                for r in await c.execute(
                    text(
                        "SELECT chapter_id, position, "
                        "scene_request->>'planned_beat_id' FROM scenes "
                        "WHERE chapter_id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            }
        assert linked == {
            (edited, 0): beats[0]["node_id"],
            (edited, 1): None,
            (wrapped, 0): got[wrapped][3][0]["node_id"],
            (tail, 0): None,
        }

        # (viii) generate-planned skips the beat already written before SPEC-23.
        token = create_access_token(data={"sub": str(user_id)})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/v1/books/chapters/{edited}/scenes/generate-planned",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, r.text
        assert (r.json()["total"], r.json()["queued"], r.json()["skipped"]) == (2, 1, 1)
        async with Session() as s:
            job = await s.get(Job, uuid.UUID(r.json()["job_id"]))
            assert job.payload["request"]["planned_beat_id"] == beats[1]["node_id"]
            assert job.payload["batch"]["remaining"] == []

        # (vi) Round trip: the beats go back into the summary and come out again.
        await _alembic_cmd("downgrade", cfg, before)
        async with engine.connect() as c:
            restored = (
                await c.execute(
                    text("SELECT summary FROM chapters WHERE id = :id"), {"id": loose}
                )
            ).scalar()
            kept = (
                await c.execute(
                    text("SELECT summary FROM chapters WHERE id = :id"), {"id": lossy}
                )
            ).scalar()
        assert restored == ("Loose\n\nPlanned beats:\n- Part 1: Storm: floods\n- Bare:")
        assert kept == rows[-1][2]  # NB-2: every line, as the author had it
        await _alembic_cmd("upgrade", cfg, "head")
        again = await _chapters(engine, ids)
        for cid in ids:
            assert again[cid][1] == got[cid][1]  # title
            assert _shape(again[cid][3]) == _shape(got[cid][3])
            if cid != tail:  # trailing text ends up before the block (SPEC-23)
                assert again[cid][2] == got[cid][2]
        await _alembic_cmd("upgrade", cfg, "head")  # idempotent
    finally:
        migration_logger.removeHandler(log)
        await _alembic_cmd("upgrade", cfg, "head")
        async with engine.begin() as c:
            await c.execute(
                text(
                    "DELETE FROM jobs WHERE user_id IN (SELECT id FROM users WHERE email LIKE 'beats-%@ex.com')"
                )
            )
            await c.execute(text("DELETE FROM users WHERE email LIKE 'beats-%@ex.com'"))
        await engine.dispose()


async def test_promote_and_generate_planned_on_postgres(monkeypatch):
    """The JSONB column through the real API: promote stores the beats, the
    chapter reads them back, and generate-planned builds the first request
    from its beat."""
    import httpx

    import app.core.database as db_mod
    from app.core import estimates
    from app.core.database import get_async_db_url
    from app.core.orm_models import Book, BookPlan, Character, Job, User
    from app.core.security import create_access_token
    from app.main import app

    cfg, _ = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)

    async def _none():
        return None

    monkeypatch.setattr(estimates, "requests_left_today", _none)
    node_id, beat_id = uuid.uuid4(), uuid.uuid4()
    async with Session() as s:
        user = User(email=f"beats-{uuid.uuid4()}@ex.com", hashed_password="x")
        s.add(user)
        await s.flush()
        book = Book(user_id=user.id, title="B", synopsis="Milo and Edric.")
        s.add(book)
        await s.flush()
        milo = Character(book_id=book.id, user_id=user.id, name="Milo Voss")
        edric = Character(book_id=book.id, user_id=user.id, name="Edric Thane")
        s.add_all([milo, edric])
        await s.flush()
        s.add(
            BookPlan(
                book_id=book.id,
                kind="outline",
                content=[
                    {
                        "node_id": str(node_id),
                        "title": "Chapter 1: Intake",
                        "summary": "Milo is processed.",
                        "pov": "Milo Voss",
                        "children": [
                            {
                                "node_id": str(beat_id),
                                "title": "File",
                                "summary": "Edric reads it",
                                "character_ids": [str(edric.id)],
                                "tone": "dread",
                            }
                        ],
                    }
                ],
            )
        )
        await s.commit()
        user_id, book_id = user.id, book.id
        milo_id, edric_id = str(milo.id), str(edric.id)

    headers = {
        "Authorization": f"Bearer {create_access_token(data={'sub': str(user_id)})}"
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/v1/books/{book_id}/plans/promote",
                json={"node_id": str(node_id)},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["title"] == "Intake"
            assert r.json()["suggested_characters"] == []
            chapter_id = r.json()["chapter_id"]
            detail = (
                await client.get(
                    f"/api/v1/books/chapters/{chapter_id}", headers=headers
                )
            ).json()
            assert detail["summary"] == "Milo is processed."
            assert detail["planned_beats"][0]["node_id"] == str(beat_id)

            r = await client.post(
                f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
                json={},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            async with Session() as s:
                job = await s.get(Job, uuid.UUID(r.json()["job_id"]))
                request = job.payload["request"]
            assert request["character_ids"] == [milo_id, edric_id]
            assert request["emotional_tone"] == "dread"
            assert request["planned_beat_id"] == str(beat_id)
            assert request["scene_description"] == "File: Edric reads it"
    finally:
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": user_id})
            await c.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        await engine.dispose()
