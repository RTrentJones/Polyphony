"""httpx client that drives a running Polyphony for evals.

Auth is invite-gated, so the harness: logs in as the bootstrap admin →
mints an invite → registers a throwaway eval user → runs everything as that
user (never touching other users' data). Generation endpoints are async, so
every generate returns an id and we poll the matching GET until terminal.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time

import httpx


class EvalClientError(RuntimeError):
    pass


class PolyphonyClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base, timeout=timeout, follow_redirects=True
        )
        self._token: str | None = None
        self.user_email: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- auth ---------------------------------------------------------------
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _login(self, email: str, password: str) -> str:
        # login is OAuth2 form-encoded: username = email
        r = await self._http.post(
            "/api/v1/auth/login", data={"username": email, "password": password}
        )
        if r.status_code != 200:
            raise EvalClientError(f"login failed ({r.status_code}): {r.text[:400]}")
        return r.json()["access_token"]

    async def bootstrap_eval_user(self, admin_email: str, admin_password: str) -> str:
        """Admin-login → mint invite → register a fresh eval user; become them."""
        admin_token = await self._login(admin_email, admin_password)
        r = await self._http.post(
            "/api/v1/auth/invites",
            json={"max_uses": 1, "expires_in_days": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"invite mint failed ({r.status_code}): {r.text[:400]}"
            )
        code = r.json()["code"]
        email = f"eval+{secrets.token_hex(6)}@polyphony.eval"
        password = "eval-" + secrets.token_urlsafe(16)
        r = await self._http.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": "Eval Runner",
                "invite_code": code,
            },
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(f"register failed ({r.status_code}): {r.text[:400]}")
        self._token = r.json()["access_token"]
        self.user_email = email
        return email

    # -- low-level ----------------------------------------------------------
    async def _send(self, call, path: str, **kw) -> httpx.Response:
        # Honour the app's own rate limits (SPEC-05): on a 429 wait Retry-After
        # and try again, like the UI does, instead of failing the run.
        for _ in range(3):
            r = await call(path, headers=self._auth(), **kw)
            if r.status_code != 429:
                return r
            try:
                wait = float(r.headers.get("Retry-After", "5"))
            except ValueError:
                wait = 5.0
            await asyncio.sleep(min(max(wait, 1.0), 60.0))
        return r

    async def _post(self, path: str, **kw) -> httpx.Response:
        return await self._send(self._http.post, path, **kw)

    async def _get(self, path: str, **kw) -> httpx.Response:
        return await self._send(self._http.get, path, **kw)

    async def _put(self, path: str, **kw) -> httpx.Response:
        return await self._http.put(path, headers=self._auth(), **kw)

    async def poll(self, path: str, done, *, timeout: float = 300, interval: float = 3):
        """Poll GET `path` until `done(json)` is truthy; returns that json."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            r = await self._get(path)
            if r.status_code == 200:
                body = r.json()
                last = body
                if done(body):
                    return body
            await asyncio.sleep(interval)
        # Say what the app was doing, so a timeout is diagnosable from the
        # report alone (a quota pause and a stuck job look the same otherwise).
        raise EvalClientError(f"poll timed out: {path} — last state: {_state(last)}")

    # -- sources / characters ----------------------------------------------
    async def upload_source(
        self, filename: str, content: bytes, title: str, book_id: str | None = None
    ) -> dict:
        # No book_id → the endpoint auto-creates a book for the upload. The
        # returned dict carries the source id, its book_id and the extraction
        # run it started (extraction PROPOSES; nothing is canon until commit).
        params = {"title": title}
        if book_id:
            params["book_id"] = book_id
        r = await self._post(
            "/api/v1/sources/upload",
            files={"file": (filename, content, "text/plain")},
            params=params,
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(f"upload failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def wait_source(self, source_id: str, timeout: float = 300) -> dict:
        return await self.poll(
            f"/api/v1/sources/{source_id}",
            lambda b: b.get("status") in ("completed", "failed"),
            timeout=timeout,
        )

    async def source_characters(self, source_id: str) -> list[dict]:
        r = await self._get(f"/api/v1/sources/{source_id}/characters")
        r.raise_for_status()
        return r.json()["characters"]

    # -- extraction: propose -> review -> commit -----------------------------
    async def wait_extraction(
        self, book_id: str, run_id: str, timeout: float = 600
    ) -> dict:
        """Poll an extraction run until it has proposals (or failed)."""
        return await self.poll(
            f"/api/v1/books/{book_id}/extractions/{run_id}",
            lambda b: b.get("status") in ("ready", "failed", "committed"),
            timeout=timeout,
        )

    async def commit_extraction(
        self, book_id: str, run_id: str, proposals: dict, *, synopsis: bool = False
    ) -> dict:
        """Approve every proposed character and canon entry exactly as proposed
        (what an author who clicks "approve all" does)."""
        body: dict = {
            "characters": proposals.get("characters", []),
            "canon_entries": proposals.get("canon_entries", []),
        }
        if synopsis and proposals.get("synopsis"):
            body["synopsis"] = proposals["synopsis"]
        r = await self._post(
            f"/api/v1/books/{book_id}/extractions/{run_id}/commit", json=body
        )
        if r.status_code != 200:
            raise EvalClientError(f"commit failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def extract_and_commit(
        self, filename: str, content: bytes, title: str
    ) -> tuple[dict, dict]:
        """Upload → wait for proposals → approve all. Returns (upload, run)."""
        up = await self.upload_source(filename, content, title)
        run = await self.wait_extraction(up["book_id"], up["extraction_run_id"])
        if run.get("status") != "ready":
            raise EvalClientError(
                f"extraction {run.get('status')}: {run.get('error') or ''}"
            )
        await self.commit_extraction(
            up["book_id"], up["extraction_run_id"], run.get("proposals") or {}
        )
        return up, run

    async def wait_voice_indexed(
        self, source_id: str, timeout: float = 300
    ) -> list[dict]:
        """Poll the source's cast until every character has been voice-indexed."""
        deadline = time.monotonic() + timeout
        chars: list[dict] = []
        while time.monotonic() < deadline:
            chars = await self.source_characters(source_id)
            if chars and all(c.get("indexed_at") for c in chars):
                return chars
            await asyncio.sleep(2)
        return chars

    async def get_character(self, character_id: str) -> dict:
        r = await self._get(f"/api/v1/characters/{character_id}")
        r.raise_for_status()
        return r.json()

    async def create_character(self, name: str, book_id: str, **fields) -> dict:
        r = await self._post(
            "/api/v1/characters/", json={"name": name, "book_id": book_id, **fields}
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"create character failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

    async def add_voice_samples(
        self, character_id: str, samples: list[str], chunk_type="dialogue"
    ) -> dict:
        r = await self._post(
            f"/api/v1/characters/{character_id}/voice-samples",
            json={"samples": samples, "chunk_type": chunk_type},
        )
        r.raise_for_status()
        return r.json()

    async def test_dialogue(
        self, character_id: str, prompt: str, context: str | None = None
    ) -> dict:
        r = await self._post(
            f"/api/v1/characters/{character_id}/test-dialogue",
            json={"prompt": prompt, "context": context},
        )
        if r.status_code != 200:
            raise EvalClientError(
                f"test-dialogue failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

    # -- books / plans / continuity ----------------------------------------
    async def create_book(self, title: str, synopsis: str, genre: str = "") -> dict:
        r = await self._post(
            "/api/v1/books/",
            json={"title": title, "synopsis": synopsis, "genre": genre},
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"create book failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

    async def generate_outline(
        self, book_id: str, chapters_target: int = 8, timeout: float = 900
    ) -> dict:
        """Generate an outline and return the finished plan.

        The staged generator runs as a background job and answers with a job
        handle, so poll the book's plans until the outline is no longer
        generating. (Reading `content` straight off the POST response is what
        pinned the outline eval at 0 after the staged generator shipped.)"""
        r = await self._post(
            f"/api/v1/books/{book_id}/plans/generate",
            json={"kind": "outline", "chapters_target": chapters_target},
        )
        if r.status_code != 200:
            raise EvalClientError(f"outline failed ({r.status_code}): {r.text[:400]}")
        started = r.json()
        if "content" in started and started.get("status", "ready") == "ready":
            return started
        plans = await self.poll(
            f"/api/v1/books/{book_id}/plans",
            lambda b: any(
                p.get("kind") == "outline"
                and p.get("status") in ("ready", "failed", "awaiting_confirmation")
                for p in b.get("plans", [])
            ),
            timeout=timeout,
        )
        plan = next(p for p in plans["plans"] if p.get("kind") == "outline")
        if plan.get("status") == "awaiting_confirmation":
            await self._post(
                f"/api/v1/books/{book_id}/plans/{plan['id']}/confirm-premise", json={}
            )
            return await self.generate_outline_wait(book_id, timeout)
        return plan

    async def generate_outline_wait(self, book_id: str, timeout: float = 900) -> dict:
        plans = await self.poll(
            f"/api/v1/books/{book_id}/plans",
            lambda b: any(
                p.get("kind") == "outline" and p.get("status") in ("ready", "failed")
                for p in b.get("plans", [])
            ),
            timeout=timeout,
        )
        return next(p for p in plans["plans"] if p.get("kind") == "outline")

    async def create_chapter(self, book_id: str, title: str, summary: str = "") -> dict:
        r = await self._post(
            f"/api/v1/books/{book_id}/chapters",
            json={"title": title, "summary": summary},
        )
        r.raise_for_status()
        return r.json()

    async def generate_scene(self, chapter_id: str, body: dict) -> dict:
        r = await self._post(
            f"/api/v1/books/chapters/{chapter_id}/scenes/generate", json=body
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"scene generate failed ({r.status_code}): {r.text[:400]}"
            )
        started = r.json()
        scene = await self.poll(
            f"/api/v1/scenes/{started['scene_id']}",
            lambda b: b.get("status") in ("completed", "failed"),
        )
        # Which mode actually ran (the tier may downgrade ensemble — SPEC-12).
        scene["mode_used"] = started.get("mode_used")
        return scene

    async def create_scene(
        self, chapter_id: str, content: str = "", characters: list[str] | None = None
    ) -> dict:
        """Create a scene WITHOUT generation (POST /chapters/{id}/scenes).

        Used to scaffold continuity-eval scenes cheaply — the harness supplies
        its own injected/control prose, so paying for a full generation only to
        overwrite it would burn free-tier quota for nothing."""
        r = await self._post(
            f"/api/v1/books/chapters/{chapter_id}/scenes",
            json={"content": content, "characters": characters or []},
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"create scene failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

    async def set_scene_content(self, scene_id: str, content: str) -> dict:
        """Overwrite a scene's prose (used to seed continuity-eval content).

        The route is PUT /books/scenes/{id}/content — POST 405s."""
        r = await self._put(
            f"/api/v1/books/scenes/{scene_id}/content", json={"content": content}
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"set scene content failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

    async def run_continuity(
        self, book_id: str, chapter_id: str | None = None
    ) -> list[dict]:
        r = await self._post(
            f"/api/v1/books/{book_id}/continuity", json={"chapter_id": chapter_id}
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(
                f"continuity failed ({r.status_code}): {r.text[:400]}"
            )
        report_id = r.json()["report_id"]
        reports = await self.poll(
            f"/api/v1/books/{book_id}/continuity",
            lambda b: any(
                rp["id"] == report_id and rp["status"] in ("completed", "failed")
                for rp in b.get("reports", [])
            ),
        )
        rp = next(r for r in reports["reports"] if r["id"] == report_id)
        return rp.get("findings", []) or []


def _state(body) -> str:
    """The status-ish fields of a polled body (or of each item in a list)."""
    keys = ("kind", "status", "stage", "error", "job")
    if isinstance(body, dict):
        items = body.get("plans") or body.get("reports") or [body]
    elif isinstance(body, list):
        items = body
    else:
        return repr(body)[:300]
    out = [
        {k: item.get(k) for k in keys if item.get(k) is not None}
        for item in items
        if isinstance(item, dict)
    ]
    return json.dumps(out, default=str)[:600]
