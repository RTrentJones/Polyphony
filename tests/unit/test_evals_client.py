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
