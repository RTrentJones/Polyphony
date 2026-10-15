"""The relay LLM queues each request and returns whatever the answerer writes."""

import asyncio
import json

import httpx
import pytest

pytestmark = pytest.mark.unit


async def test_a_request_waits_for_its_answer(tmp_path, monkeypatch):
    from evals import relay_llm

    monkeypatch.setenv("RELAY_DIR", str(tmp_path))
    transport = httpx.ASGITransport(app=relay_llm.app)

    async def answerer():
        while not relay_llm._pending():
            await asyncio.sleep(0.05)
        (req,) = relay_llm._pending()
        body = json.loads((tmp_path / "pending" / f"{req['id']}.json").read_text())
        assert body["purpose"] == "You are a strict judge."
        assert body["messages"][-1]["content"] == "score this"
        (tmp_path / "done" / f"{req['id']}.json").write_text(
            json.dumps({"content": '{"score": 0.7}'})
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://relay") as c:
        task = asyncio.create_task(answerer())
        r = await c.post(
            "/v1/chat/completions",
            json={
                "model": "m",
                "messages": [
                    {"role": "system", "content": "You are a strict judge."},
                    {"role": "user", "content": "score this"},
                ],
            },
        )
        await task
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == '{"score": 0.7}'
    # The exchange is archived and the queue is empty again.
    assert relay_llm._pending() == []
    (archived,) = (tmp_path / "answered").glob("*.json")
    assert json.loads(archived.read_text())["answer"] == '{"score": 0.7}'


async def test_an_unanswered_request_times_out(tmp_path, monkeypatch):
    from evals import relay_llm

    monkeypatch.setenv("RELAY_DIR", str(tmp_path))
    monkeypatch.setenv("RELAY_TIMEOUT_SECONDS", "0")
    transport = httpx.ASGITransport(app=relay_llm.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://relay") as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}]},
        )
    assert r.status_code == 504
    assert relay_llm._pending() == []
