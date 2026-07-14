"""Endpoints that used to fire BackgroundTasks must now enqueue durable jobs.

No worker runs in these tests (lifespan never starts under ASGITransport), so
the domain row stays 'processing' and the assertion is: response contract
unchanged + a matching jobs row committed alongside the domain row.
"""

import pytest
from sqlalchemy import select

from app.core.orm_models import Job


async def _jobs(async_session, kind):
    return (
        (await async_session.execute(select(Job).where(Job.kind == kind)))
        .scalars()
        .all()
    )


@pytest.mark.integration
class TestGenerateSceneEnqueues:
    @pytest.mark.asyncio
    async def test_scene_generate_creates_job(
        self, client, auth_headers, test_manuscript, test_user, async_session
    ):
        r = await client.post(
            "/api/v1/scenes/generate",
            json={
                "manuscript_id": str(test_manuscript.id),
                "characters": ["Mina"],
                "scene_description": "A quiet talk at dusk on the terrace.",
                "setting": "terrace",
                "emotional_tone": "wistful",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing"

        jobs = await _jobs(async_session, "generate_scene")
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "queued"
        assert job.user_id == test_user.id
        assert job.payload["scene_id"] == body["scene_id"]
        assert job.payload["user_id"] == str(test_user.id)
        assert job.payload["request"]["characters"] == ["Mina"]


@pytest.mark.integration
class TestChapterSceneGenerateEnqueues:
    @pytest.mark.asyncio
    async def test_chapter_generate_creates_prose_job(
        self, client, auth_headers, test_user, async_session
    ):
        book = (
            await client.post(
                "/api/v1/books/",
                json={"title": "B", "synopsis": "s"},
                headers=auth_headers,
            )
        ).json()
        chapter = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C1", "summary": "The beginning."},
                headers=auth_headers,
            )
        ).json()

        r = await client.post(
            f"/api/v1/books/chapters/{chapter['id']}/scenes/generate",
            json={
                "characters": ["Mina"],
                "scene_description": "The chapter opens with a storm.",
                "setting": "moor",
                "emotional_tone": "ominous",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing"

        jobs = await _jobs(async_session, "generate_prose_scene")
        assert len(jobs) == 1
        job = jobs[0]
        assert job.payload["scene_id"] == body["scene_id"]
        assert job.payload["book_id"] == book["id"]
        assert job.payload["chapter_summary"] == "The beginning."


@pytest.mark.integration
class TestManuscriptEnqueues:
    @pytest.mark.asyncio
    async def test_upload_creates_process_job(
        self, client, auth_headers, test_user, async_session
    ):
        r = await client.post(
            "/api/v1/manuscripts/upload",
            files={"file": ("story.txt", b"Once upon a time.", "text/plain")},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing"

        jobs = await _jobs(async_session, "process_manuscript")
        assert len(jobs) == 1
        job = jobs[0]
        assert job.max_attempts == 2
        assert job.payload == {
            "manuscript_id": body["id"],
            "user_id": str(test_user.id),
        }

    @pytest.mark.asyncio
    async def test_reprocess_creates_job(
        self, client, auth_headers, test_user, async_session
    ):
        from app.core.orm_models import Manuscript

        ms = Manuscript(
            user_id=test_user.id,
            title="M",
            content_hash="deadbeef",
            content_text="Some prose.",
            status="failed",
        )
        async_session.add(ms)
        await async_session.commit()

        r = await client.post(
            f"/api/v1/manuscripts/{ms.id}/process", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json()["status"] == "processing"

        jobs = await _jobs(async_session, "process_manuscript")
        assert len(jobs) == 1
        assert jobs[0].payload["manuscript_id"] == str(ms.id)


@pytest.mark.integration
class TestContinuityEnqueues:
    @pytest.mark.asyncio
    async def test_continuity_creates_job(
        self, client, auth_headers, test_user, async_session
    ):
        from uuid import UUID

        from app.core.orm_models import Scene

        book = (
            await client.post(
                "/api/v1/books/", json={"title": "B"}, headers=auth_headers
            )
        ).json()
        chapter = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C1"},
                headers=auth_headers,
            )
        ).json()
        async_session.add(
            Scene(
                user_id=test_user.id,
                chapter_id=UUID(chapter["id"]),
                position=0,
                status="completed",
                generated_content="Some finished prose to check.",
                content="Some finished prose to check.",
            )
        )
        await async_session.commit()

        r = await client.post(
            f"/api/v1/books/{book['id']}/continuity",
            json={},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing"

        jobs = await _jobs(async_session, "continuity_check")
        assert len(jobs) == 1
        assert jobs[0].payload == {
            "report_id": body["report_id"],
            "user_id": str(test_user.id),
        }
