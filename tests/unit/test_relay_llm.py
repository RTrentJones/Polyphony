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


async def test_the_purpose_header_names_the_request(tmp_path, monkeypatch):
    """A generation call has no system line; the app's X-Polyphony-Purpose
    header is what files it (it used to be filed under an empty purpose)."""
    from evals import relay_llm

    monkeypatch.setenv("RELAY_DIR", str(tmp_path))
    transport = httpx.ASGITransport(app=relay_llm.app)
    seen = {}

    async def answerer():
        while not relay_llm._pending():
            await asyncio.sleep(0.05)
        (req,) = relay_llm._pending()
        seen["purpose"] = req["purpose"]
        (tmp_path / "done" / f"{req['id']}.json").write_text(
            json.dumps({"content": "prose"})
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://relay") as c:
        task = asyncio.create_task(answerer())
        r = await c.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "write the beat"}]},
            headers={"X-Polyphony-Purpose": "beat_prose"},
        )
        await task
    assert r.status_code == 200 and seen["purpose"] == "beat_prose"
    (archived,) = (tmp_path / "answered").glob("*.json")
    assert json.loads(archived.read_text())["purpose"] == "beat_prose"


def test_purpose_prefers_the_header_then_the_system_line():
    from evals.relay_llm import _purpose

    msgs = [{"role": "system", "content": "You are a judge.\nmore"}]
    assert _purpose(msgs, "eval_judge") == "eval_judge"
    assert _purpose(msgs, "  ") == "You are a judge."
    assert _purpose([{"role": "user", "content": "x"}]) == ""


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


async def test_the_recorder_archives_answers_a_429_and_no_header(tmp_path, monkeypatch):
    from evals import relay_llm

    env = dict(RELAY_DIR=tmp_path, RELAY_UPSTREAM="http://v", RELAY_TIMEOUT_SECONDS=0)
    [monkeypatch.setenv(k, str(v)) for k, v in env.items()]  # base: 504, no wait
    ok, q, real = {"choices": [{"message": {"content": "p"}}]}, "429", httpx.AsyncClient
    rs = iter([httpx.Response(200, json=ok), httpx.Response(429, text=q)])
    ask = {"Authorization": "Bearer k", "X-Polyphony-Purpose": "beat_prose"}
    a = ask["Authorization"]
    up = httpx.MockTransport(lambda r: r.headers["authorization"] == a and next(rs))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: real(transport=up))
    async with real(transport=httpx.ASGITransport(app=relay_llm.app)) as c:
        r = [await c.post("http://r/v1/chat/completions", json={}, headers=ask)]
        r += [await c.post("http://r/v1/chat/completions", json={}, headers=ask)]
    assert (r[1].status_code, r[1].text) == (429, q)
    texts = [p.read_text() for p in sorted(tmp_path.glob("answered/*.json"))]
    assert [json.loads(t)["answer"] for t in texts] == ["p", q] and "beat_" in texts[1]
    assert not any("Bearer" in t for t in texts)  # no header is written
