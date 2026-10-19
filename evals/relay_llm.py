"""A relay LLM: an OpenAI-compatible endpoint answered by an agent, not a vendor.

Each chat completion the app (or the eval judge) sends is written to a queue
directory as a JSON file, and the request blocks until an answer file appears.
Whoever watches the queue answers it. In practice that is a Claude agent
playing the model, so live-quality generations and judgments run without a
vendor key or the Gemini free-tier quota.

Point the app at it through the stub provider, which already takes any
OpenAI-compatible base URL:

    LLM_PROVIDER=stub STUB_LLM_API_KEY=x \\
    STUB_LLM_BASE_URL=http://127.0.0.1:8299/v1 LLM_TIMEOUT_SECONDS=1800 ...

and the eval judge the same way (EVAL_JUDGE_PROVIDER=stub).

Queue layout (RELAY_DIR, default ./relay-queue):

- ``pending/<id>.json``: ``{id, model, purpose, messages, max_tokens,
  response_format, created}``, written when a request arrives.
- ``done/<id>.json``: ``{"content": "..."}``, written by the answerer. The relay
  returns it and moves the request to ``answered/``.

Answer from a shell with ``python -m evals.relay_llm answer <id> <file>`` (or
write ``done/<id>.json`` directly), and list the queue with
``python -m evals.relay_llm pending``.

Run: ``python -m evals.relay_llm serve --port 8299``. Recorder (``RELAY_UPSTREAM``):
forwards with the caller's Authorization, archives, never a header (W13-3).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="polyphony-relay-llm")
_SEQ = itertools.count(1)


def _dir() -> Path:
    return Path(os.getenv("RELAY_DIR", "relay-queue"))


def _paths(rid: str) -> tuple[Path, Path, Path]:
    base = _dir()
    for sub in ("pending", "done", "answered"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return (
        base / "pending" / f"{rid}.json",
        base / "done" / f"{rid}.json",
        base / "answered" / f"{rid}.json",
    )


def _purpose(messages: list[dict], header: str | None = None) -> str:
    """What the call is for: the app's X-Polyphony-Purpose header (app/llm/
    client.py sends it on every call), else the system prompt's first line.

    Generation calls carry no system message, so before the header every one
    of them was filed under an empty purpose."""
    if header and header.strip():
        return header.strip()[:120]
    for m in messages:
        if m.get("role") == "system" and m.get("content"):
            return str(m["content"]).strip().splitlines()[0][:120]
    return ""


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    rid = f"{int(time.time())}-{next(_SEQ):04d}"
    pending, done, answered = _paths(rid)
    messages = body.get("messages", [])
    if upstream := os.getenv("RELAY_UPSTREAM"):  # the recorder (W13-3)
        auth = {"Authorization": request.headers.get("authorization", "")}
        async with httpx.AsyncClient(timeout=None) as c:  # the app bounds it
            r = await c.post(f"{upstream}/chat/completions", json=body, headers=auth)
        ans = r.json()["choices"][0]["message"]["content"] if r.is_success else r.text
        purpose = _purpose(messages, request.headers.get("x-polyphony-purpose"))
        rec = dict(id=rid, purpose=purpose, messages=messages, answer=ans)
        rec |= dict(status=r.status_code, created=time.time())
        answered.write_text(json.dumps(rec, indent=2))  # never a header
        return Response(r.content, r.status_code, {}, r.headers.get("content-type"))
    pending.write_text(
        json.dumps(
            {
                "id": rid,
                "model": body.get("model"),
                "purpose": _purpose(
                    messages, request.headers.get("x-polyphony-purpose")
                ),
                "messages": messages,
                "max_tokens": body.get("max_tokens"),
                "response_format": body.get("response_format"),
                "created": time.time(),
            },
            indent=2,
        )
    )
    deadline = time.time() + float(os.getenv("RELAY_TIMEOUT_SECONDS", "1700"))
    while not done.exists():
        if time.time() > deadline:
            pending.unlink(missing_ok=True)
            return JSONResponse(
                {"error": {"message": f"relay request {rid} not answered"}},
                status_code=504,
            )
        await asyncio.sleep(1)
    content = json.loads(done.read_text()).get("content", "")
    request_body = json.loads(pending.read_text()) if pending.exists() else {}
    answered.write_text(json.dumps({**request_body, "answer": content}, indent=2))
    pending.unlink(missing_ok=True)
    done.unlink(missing_ok=True)
    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
    return {
        "id": f"relay-{rid}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "relay"),
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": max(1, prompt_chars // 4),
            "completion_tokens": max(1, len(content) // 4),
            "total_tokens": max(1, prompt_chars // 4) + max(1, len(content) // 4),
        },
    }


@app.get("/__relay/pending")
async def pending_list():
    return {"pending": _pending()}


def _pending() -> list[dict]:
    base = _dir() / "pending"
    if not base.exists():
        return []
    out = []
    for p in sorted(base.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue  # being written
        out.append({"id": d["id"], "purpose": d.get("purpose"), "path": str(p)})
    return out


def _answer(rid: str, source: str) -> None:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    _, done, _ = _paths(rid)
    tmp = done.with_suffix(".tmp")
    tmp.write_text(json.dumps({"content": text}))
    tmp.rename(done)  # atomic: the server never reads a half-written answer


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8299)
    sub.add_parser("pending")
    a = sub.add_parser("answer")
    a.add_argument("id")
    a.add_argument("source", help="file with the answer text, or - for stdin")
    args = ap.parse_args()
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    elif args.cmd == "pending":
        for p in _pending():
            print(f"{p['id']}  {p['purpose']}  {p['path']}")
    else:
        _answer(args.id, args.source)


if __name__ == "__main__":
    main()
