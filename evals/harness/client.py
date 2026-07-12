"""httpx client that drives a running Polyphony for evals.

Auth is invite-gated, so the harness: logs in as the bootstrap admin →
mints an invite → registers a throwaway eval user → runs everything as that
user (never touching other users' data). Generation endpoints are async, so
every generate returns an id and we poll the matching GET until terminal.
"""

from __future__ import annotations

import asyncio
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
    async def _post(self, path: str, **kw) -> httpx.Response:
        return await self._http.post(path, headers=self._auth(), **kw)

    async def _get(self, path: str, **kw) -> httpx.Response:
        return await self._http.get(path, headers=self._auth(), **kw)

    async def _put(self, path: str, **kw) -> httpx.Response:
        return await self._http.put(path, headers=self._auth(), **kw)

    async def poll(self, path: str, done, *, timeout: float = 300, interval: float = 3):
        """Poll GET `path` until `done(json)` is truthy; returns that json."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = await self._get(path)
            if r.status_code == 200:
                body = r.json()
                if done(body):
                    return body
            await asyncio.sleep(interval)
        raise EvalClientError(f"poll timed out: {path}")

    # -- manuscripts / characters ------------------------------------------
    async def upload_manuscript(
        self, filename: str, content: bytes, title: str
    ) -> dict:
        r = await self._post(
            "/api/v1/manuscripts/upload",
            files={"file": (filename, content, "text/plain")},
            params={"title": title},
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(f"upload failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def wait_manuscript(self, manuscript_id: str, timeout: float = 300) -> dict:
        return await self.poll(
            f"/api/v1/manuscripts/{manuscript_id}",
            lambda b: b.get("status") in ("completed", "failed"),
            timeout=timeout,
        )

    async def manuscript_characters(self, manuscript_id: str) -> list[dict]:
        r = await self._get(f"/api/v1/manuscripts/{manuscript_id}/characters")
        r.raise_for_status()
        return r.json()["characters"]

    async def get_character(self, character_id: str) -> dict:
        r = await self._get(f"/api/v1/characters/{character_id}")
        r.raise_for_status()
        return r.json()

    async def create_character(self, name: str, **fields) -> dict:
        r = await self._post("/api/v1/characters/", json={"name": name, **fields})
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

    async def generate_outline(self, book_id: str, chapters_target: int = 8) -> dict:
        r = await self._post(
            f"/api/v1/books/{book_id}/plans/generate",
            json={"kind": "outline", "chapters_target": chapters_target},
        )
        if r.status_code != 200:
            raise EvalClientError(f"outline failed ({r.status_code}): {r.text[:400]}")
        return r.json()

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
        return await self.poll(
            f"/api/v1/scenes/{started['scene_id']}",
            lambda b: b.get("status") in ("completed", "failed"),
        )

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
