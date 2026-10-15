"""SPEC-23 on real Postgres: the `w2_planned_beats` migration.

* `chapters.planned_beats` is JSONB after upgrade, gone after a downgrade to the
  revision before it.
* The backfill: a chapter promoted from a node still in the outline takes that
  node's children (node_ids and new fields kept); any other chapter has its
  "- title: summary" lines parsed. The summary keeps only its own text.
* Promoted titles lose a leading "Chapter N:", unless that empties them.
* upgrade -> downgrade -> upgrade leaves the same summaries and beats.

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

REVISION = "w2_planned_beats"


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


async def test_migration_backfills_strips_titles_and_round_trips():
    from app.core.database import get_async_db_url
    from app.core.orm_models import Book, BookPlan, User

    cfg, before = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    node_id, gone_node, c1, c2 = (uuid.uuid4() for _ in range(4))
    zara = str(uuid.uuid4())
    try:
        assert await _column_type(engine) == "jsonb"
        async with Session() as s:
            user = User(email=f"beats-{uuid.uuid4()}@ex.com", hashed_password="x")
            s.add(user)
            await s.flush()
            book = Book(user_id=user.id, title="B", synopsis="s")
            s.add(book)
            await s.flush()
            s.add(
                BookPlan(
                    book_id=book.id,
                    kind="outline",
                    content=[
                        {
                            "node_id": str(node_id),
                            "title": "Chapter 2: Milo Voss",
                            "summary": "Milo arrives",
                            "children": [
                                {
                                    "node_id": str(c1),
                                    "title": "Badge photo",
                                    "summary": "he is dead",
                                    "setting": "Intake Atrium",
                                    "tone": "dread",
                                    "character_ids": [zara],
                                    "secondary_names": ["Prava"],
                                    "children": [],
                                },
                                {
                                    "node_id": str(c2),
                                    "title": "Lunch",
                                    "summary": "",
                                    "children": [],
                                },
                            ],
                        }
                    ],
                )
            )
            await s.commit()
            book_id = book.id

        await _alembic_cmd("downgrade", cfg, before)
        assert await _column_type(engine) is None

        # Chapters as the pre-SPEC-23 promote wrote them.
        promoted, loose, orphan = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        rows = [
            (
                promoted,
                0,
                "Chapter 2: Milo Voss",
                "Milo arrives\n\nPlanned beats:\n- Badge photo: he is dead\n- Lunch:",
                node_id,
            ),
            (
                loose,
                1,
                "Chapter 3: Hand-made",  # no plan_node_id: title kept
                "Loose\n\nPlanned beats:\n- Part 1: Storm: floods\n- Bare",
                None,
            ),
            (
                orphan,
                2,
                "Chapter 4:",  # stripping would empty it: kept
                "Gone\n\nPlanned beats:\n- Memo: a decree",
                gone_node,  # its node is no longer in the outline
            ),
        ]
        async with engine.begin() as c:
            for cid, pos, title, summary, plan_node in rows:
                await c.execute(
                    text(
                        "INSERT INTO chapters (id, book_id, position, title, "
                        "summary, status, plan_node_id) VALUES (:id, :b, :p, :t, "
                        ":s, 'drafting', :n)"
                    ),
                    {
                        "id": cid,
                        "b": book_id,
                        "p": pos,
                        "t": title,
                        "s": summary,
                        "n": plan_node,
                    },
                )

        await _alembic_cmd("upgrade", cfg, "head")
        assert await _column_type(engine) == "jsonb"
        got = await _chapters(engine, [promoted, loose, orphan])

        _, title, summary, beats = got[promoted]
        assert title == "Milo Voss"
        assert summary == "Milo arrives"
        assert [b["node_id"] for b in beats] == [str(c1), str(c2)]  # from the plan
        assert beats[0]["setting"] == "Intake Atrium" and beats[0]["tone"] == "dread"
        assert beats[0]["character_ids"] == [zara]
        assert beats[0]["secondary_names"] == ["Prava"]

        _, title, summary, beats = got[loose]
        assert title == "Chapter 3: Hand-made"
        assert summary == "Loose"
        assert _shape(beats) == [("Part 1", "Storm: floods"), ("Bare", "")]
        assert all(uuid.UUID(b["node_id"]) for b in beats)

        _, title, summary, beats = got[orphan]
        assert title == "Chapter 4:"
        assert summary == "Gone"
        assert _shape(beats) == [("Memo", "a decree")]

        # Round trip: the beats go back into the summary and come out again.
        await _alembic_cmd("downgrade", cfg, before)
        async with engine.connect() as c:
            restored = (
                await c.execute(
                    text("SELECT summary FROM chapters WHERE id = :id"),
                    {"id": loose},
                )
            ).scalar()
        assert restored == ("Loose\n\nPlanned beats:\n- Part 1: Storm: floods\n- Bare:")
        await _alembic_cmd("upgrade", cfg, "head")
        again = await _chapters(engine, [promoted, loose, orphan])
        for cid in (promoted, loose, orphan):
            assert again[cid][1] == got[cid][1]  # title
            assert again[cid][2] == got[cid][2]  # summary
            assert _shape(again[cid][3]) == _shape(got[cid][3])
        assert [b["node_id"] for b in again[promoted][3]] == [str(c1), str(c2)]
        await _alembic_cmd("upgrade", cfg, "head")  # idempotent
    finally:
        await _alembic_cmd("upgrade", cfg, "head")
        async with engine.begin() as c:
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
