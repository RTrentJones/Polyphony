"""SPEC-19 (backlog B3) on real Postgres.

* The migration: `scene_revisions.meta` is JSONB after upgrade, gone after a
  downgrade to the revision before it, and back on re-upgrade.
* The round trip through the real worker: redo a beat → the job stores a
  proposal (JSONB meta) → accept → the draft outside the beat is
  byte-identical and exactly one snapshot of the old draft was appended. The
  redo keeps what the chapter's later scene refers to, not a trashed one's.

Skipped unless RUN_PG_TESTS is set (CI provides a pgvector service).
"""

import asyncio
import os
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUN_PG_TESTS"),
        reason="Postgres integration tests only run when RUN_PG_TESTS is set",
    ),
]

REVISION = "b3_scene_revise"


def _alembic():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    # Resolved, not hard-coded: the coordinator may re-chain migrations.
    before = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
    return cfg, before


async def _alembic_cmd(name, cfg, target):
    from alembic import command

    await asyncio.to_thread(getattr(command, name), cfg, target)


async def _meta_type(engine):
    async with engine.connect() as c:
        return (
            await c.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='scene_revisions' "
                    "AND column_name='meta'"
                )
            )
        ).scalar()


async def test_migration_upgrade_downgrade_round_trip():
    from app.core.database import get_async_db_url

    cfg, before = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    try:
        assert await _meta_type(engine) == "jsonb"
        await _alembic_cmd("downgrade", cfg, before)
        assert await _meta_type(engine) is None
        await _alembic_cmd("upgrade", cfg, "head")
        assert await _meta_type(engine) == "jsonb"
        await _alembic_cmd("upgrade", cfg, "head")  # idempotent
    finally:
        await _alembic_cmd("upgrade", cfg, "head")
        await engine.dispose()


class _FakeLLM:
    def __init__(self):
        self.prompts = []

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.append((purpose, messages[0]["content"]))
        return types.SimpleNamespace(
            text="Zara said nothing at all.", tokens_in=1, tokens_out=1
        )


class _NoStore:
    async def retrieve_similar(self, **kw):
        return []


async def _none():
    return None


async def test_revise_accept_round_trip_through_the_worker(monkeypatch):
    import httpx

    import app.characters.context as ctx_mod
    import app.core.database as db_mod
    import app.jobs.worker as worker_mod
    from app.core import estimates
    from app.core.database import get_async_db_url
    from app.core.orm_models import Book, Chapter, Job, Scene, SceneRevision, User
    from app.core.security import create_access_token
    from app.jobs import revise as revise_job
    from app.jobs.handlers import HANDLERS, Handler
    from app.jobs.worker import JobWorker
    from app.main import app
    from app.orchestration import revise as rv
    from tests.unit.factories import add_scene, add_scene_beats

    cfg, _ = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)
    fake = _FakeLLM()
    monkeypatch.setattr(rv, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: _NoStore())
    monkeypatch.setattr(estimates, "requests_left_today", _none)
    handler = HANDLERS.get(revise_job.KIND) or Handler(
        run=revise_job.run,
        on_dead=revise_job.on_dead,
        on_pause=revise_job.on_pause,
        on_resume=revise_job.on_resume,
        on_cancel=revise_job.on_cancel,
    )
    monkeypatch.setattr(worker_mod, "HANDLERS", {revise_job.KIND: handler})

    p1, p2, p3 = (
        "Milo kept his eyes on the ledger.",
        "Zara leaned over the desk and tapped Form K-9 and the Harrow Ledger.",
        "Outside, the lifts sighed.",
    )
    draft = f"{p1}\n\n{p2}\n\n{p3}"
    async with Session() as s:
        user = User(email=f"revise-{uuid.uuid4()}@ex.com", hashed_password="x")
        s.add(user)
        await s.flush()
        book = Book(user_id=user.id, title="B", synopsis="Milo and Zara.")
        s.add(book)
        await s.flush()
        chapter = Chapter(book_id=book.id, title="One", position=0, summary="Audit.")
        s.add(chapter)
        await s.flush()
        scene = Scene(
            user_id=user.id,
            book_id=book.id,
            chapter_id=chapter.id,
            position=0,
            status="completed",
            generated_content=draft,
            content=draft,
        )
        s.add(scene)
        await s.flush()
        for beat, prose in zip(await add_scene_beats(s, scene, ["b0", "b1"]), (p1, p2)):
            beat.content = prose
        await add_scene(s, book, chapter, 1, content="Form K-9, again.")
        gone = datetime.now(timezone.utc)  # SPEC-41: trashed prose is not context
        await add_scene(s, book, chapter, 2, content="Harrow Ledger", deleted_at=gone)
        await s.commit()
        user_id, scene_id = user.id, scene.id

    headers = {
        "Authorization": f"Bearer {create_access_token(data={'sub': str(user_id)})}"
    }
    start = draft.index(p2)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                f"/api/v1/books/scenes/{scene_id}/revise",
                json={
                    "instruction": "Colder.",
                    "beat_index": 1,
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            job_id = uuid.UUID(r.json()["job_id"])

            worker = JobWorker(
                poll_interval=0.01, stale_after=timedelta(minutes=30), worker_id="pg"
            )
            # The shared test database may hold other tests' queued jobs; the
            # worker claims oldest first, so run until ours has been executed.
            for _ in range(50):
                assert await worker.run_once() is True
                async with Session() as s:
                    job = await s.get(Job, job_id)
                    if job.status not in ("queued", "running"):
                        break
            assert job.status == "succeeded", job.error
            assert [p for p, _ in fake.prompts] == ["scene_revise"]
            assert "refers to them): Form K-9\n" in fake.prompts[0][1]

            listing = (
                await client.get(
                    f"/api/v1/books/scenes/{scene_id}/proposals", headers=headers
                )
            ).json()
            [proposal] = listing["proposals"]
            assert proposal["status"] == "proposed"
            r = await client.post(
                f"/api/v1/books/scenes/{scene_id}/revisions/{proposal['id']}/accept",
                headers=headers,
            )
            assert r.status_code == 200, r.text

        async with Session() as s:
            scene = await s.get(Scene, scene_id)
            assert scene.content == f"{p1}\n\nZara said nothing at all.\n\n{p3}"
            assert scene.content[:start] == draft[:start]
            assert scene.content.endswith(draft[start + len(p2) :])
            revisions = (
                (
                    await s.execute(
                        select(SceneRevision).where(SceneRevision.scene_id == scene_id)
                    )
                )
                .scalars()
                .all()
            )
            by_source = sorted((r.source, r.content) for r in revisions)
            assert by_source == [
                ("ai_revise", scene.content),
                ("edited", draft),
            ]
            accepted = next(r for r in revisions if r.source == "ai_revise")
            assert accepted.meta["status"] == "accepted"
            # JSONB on Postgres: the meta is queryable server-side.
            assert (
                await s.execute(
                    text(
                        "SELECT count(*) FROM scene_revisions "
                        "WHERE scene_id = :id AND meta->>'status' = 'accepted'"
                    ),
                    {"id": scene_id},
                )
            ).scalar() == 1
            await s.delete(await s.get(User, user_id))
            await s.commit()
    finally:
        await engine.dispose()


async def test_accept_racing_a_manual_save_sees_the_save(monkeypatch):
    """Codex P1 on #38: accept must lock the scene before it reads the draft.
    A save holding the scene's lock commits new prose; the accept that waited
    then sees that prose, so the hash check refuses it (409) instead of
    overwriting the save without a snapshot."""
    import httpx

    import app.core.database as db_mod
    from app.core.database import get_async_db_url
    from app.core.orm_models import Book, Chapter, Scene, SceneRevision, User
    from app.core.security import create_access_token
    from app.main import app
    from app.orchestration import revise as rv

    cfg, _ = _alembic()
    await _alembic_cmd("upgrade", cfg, "head")
    engine = create_async_engine(get_async_db_url())
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "get_session_factory", lambda: Session)
    draft, saved = "Milo kept his eyes on the ledger.", "The author's own line."
    async with Session() as s:
        user = User(email=f"race-{uuid.uuid4()}@ex.com", hashed_password="x")
        s.add(user)
        await s.flush()
        book = Book(user_id=user.id, title="B", synopsis="Milo.")
        s.add(book)
        await s.flush()
        chapter = Chapter(book_id=book.id, title="One", position=0, summary="A.")
        s.add(chapter)
        await s.flush()
        scene = Scene(
            user_id=user.id,
            book_id=book.id,
            chapter_id=chapter.id,
            position=0,
            status="completed",
            content=draft,
        )
        s.add(scene)
        await s.flush()
        proposal = SceneRevision(
            scene_id=scene.id,
            content="Milo looked away.",
            word_count=3,
            source="ai_revise",
            meta={"status": "proposed", "base_text_hash": rv.text_hash(draft)},
        )
        s.add(proposal)
        await s.commit()
        user_id, scene_id, proposal_id = user.id, scene.id, proposal.id

    headers = {
        "Authorization": f"Bearer {create_access_token(data={'sub': str(user_id)})}"
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with Session() as saver:
                row = (
                    await saver.execute(
                        select(Scene).where(Scene.id == scene_id).with_for_update()
                    )
                ).scalar_one()
                accept = asyncio.create_task(
                    client.post(
                        f"/api/v1/books/scenes/{scene_id}/revisions/"
                        f"{proposal_id}/accept",
                        headers=headers,
                    )
                )
                await asyncio.sleep(0.5)
                assert not accept.done()
                row.content = saved
                await saver.commit()
            r = await accept
            assert r.status_code == 409, r.text
        async with Session() as s:
            assert (await s.get(Scene, scene_id)).content == saved
    finally:
        await engine.dispose()
