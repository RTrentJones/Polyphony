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
