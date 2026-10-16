"""Unit tests for the eval PolyphonyClient — method/route regressions.

These pin the HTTP method + path against the app's routes so a client/API drift
(like the continuity 405: POST to a PUT-only route) fails here, not mid-run.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from evals.harness.client import PolyphonyClient

pytestmark = pytest.mark.unit


def _resp(status=200, json_body=None):
    return httpx.Response(
        status,
        json=json_body if json_body is not None else {},
        request=httpx.Request("PUT", "http://t"),
    )


async def test_create_scene_posts_to_chapter_scenes():
    # Manual scene create is POST /books/chapters/{id}/scenes (no generation).
    c = PolyphonyClient("http://localhost:8000")
    post = AsyncMock(
        return_value=_resp(201, {"scene_id": "sid", "status": "completed"})
    )
    c._http.post = post
    out = await c.create_scene("cid", content="hello", characters=["Nora"])
    assert out["scene_id"] == "sid"
    path = post.await_args.args[0]
    assert path == "/api/v1/books/chapters/cid/scenes"
    assert post.await_args.kwargs["json"]["content"] == "hello"
    await c.aclose()


async def test_set_scene_content_uses_put():
    # Route is PUT /books/scenes/{id}/content — a POST 405s.
    c = PolyphonyClient("http://localhost:8000")
    put = AsyncMock(return_value=_resp(200, {"ok": True}))
    post = AsyncMock(return_value=_resp(405, {"detail": "method not allowed"}))
    c._http.put = put
    c._http.post = post
    await c.set_scene_content("sid", "some prose")
    assert put.await_count == 1 and post.await_count == 0
    path = put.await_args.args[0]
    assert path == "/api/v1/books/scenes/sid/content"
    await c.aclose()


async def test_a_429_is_waited_out_and_retried(monkeypatch):
    # SPEC-05: the app rate-limits per user; the harness honours Retry-After.
    import evals.harness.client as mod

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    c = PolyphonyClient("http://localhost:8000")
    limited = httpx.Response(
        429, headers={"Retry-After": "7"}, request=httpx.Request("GET", "http://t")
    )
    get = AsyncMock(side_effect=[limited, _resp(200, {"ok": True})])
    c._http.get = get
    r = await c._get("/api/v1/books/b/plans")
    assert r.status_code == 200 and get.await_count == 2
    assert slept == [7.0]
    await c.aclose()


def test_to_async_url_converts_driver_and_drops_libpq_params():
    from app.core.database import to_async_url

    assert to_async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert (
        to_async_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require")
        == "postgresql+asyncpg://u:p@h/db"
    )
    assert (
        to_async_url("postgresql://u@h/db?sslmode=require&application_name=x")
        == "postgresql+asyncpg://u@h/db?application_name=x"
    )
    already = "postgresql+asyncpg://u:p@h/db"
    assert to_async_url(already) == already


async def test_scene_beats_reads_the_scene_api_only():
    """W1-S9: beats come from GET /scenes/{id}; the config has no DB URL."""
    from evals.harness.config import EvalConfig

    c = PolyphonyClient("http://localhost:8000")
    beats = [{"beat_number": 0, "description": "Milo signs", "content": "He signed."}]
    get = AsyncMock(return_value=_resp(200, {"id": "s1", "beats": beats}))
    c._get = get
    assert await c.scene_beats("s1") == beats
    get.assert_awaited_once_with("/api/v1/scenes/s1")
    get.return_value = _resp(200, {"id": "s1"})
    assert await c.scene_beats("s1") == []
    assert "database_url" not in EvalConfig.__dataclass_fields__


async def test_generate_planned_waits_for_the_queued_scenes_only():
    """D16: a partial batch (SPEC-34) settles when the queued scenes are
    written; the deferred beat has no scene to wait for."""
    c = PolyphonyClient("http://localhost:8000")
    c._http.post = AsyncMock(
        return_value=_resp(200, {"total": 4, "queued": 2, "skipped": 1, "deferred": 1})
    )
    seen = {}

    async def poll(path, done, **kw):
        scenes = [{"status": "completed", "position": i} for i in range(3)]
        seen["settled"] = [done({"scenes": scenes}) for _ in range(2)]
        return {"scenes": scenes}

    c.poll = poll
    out = await c.generate_planned("cid")
    assert seen["settled"] == [False, True] and len(out["scenes"]) == 3
    await c.aclose()
