"""httpx client that drives a running Polyphony for evals.

Auth is invite-gated, so the harness: logs in as the bootstrap admin →
mints an invite → registers a throwaway eval user → runs everything as that
user (never touching other users' data). Generation endpoints are async, so
every generate returns an id and we poll the matching GET until terminal.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import os
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
        # The eval user's login, in memory only: a 401 logs in again (SPEC-68).
        self._login_as: tuple[str, str] | None = None
        self.relogins = 0

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

    async def bootstrap_eval_user(self, admin_email, admin_password, fixed=None) -> str:
        """Admin-login → mint invite → register a fresh eval user; become them.
        A resumable run's `fixed` user is logged into when the restored DB has it."""
        key = hashlib.sha256(f"{admin_password}|{fixed}".encode()).hexdigest()[:24]
        with contextlib.suppress(EvalClientError):  # never stored: derived
            self._token = fixed and await self._login(fixed, f"eval-{key}")
        if self._token:
            self.user_email, self._login_as = fixed, (fixed, f"eval-{key}")
            return fixed
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
        email = fixed or f"eval+{secrets.token_hex(6)}@polyphony.eval"
        password = f"eval-{key}" if fixed else "eval-" + secrets.token_urlsafe(16)
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
        self._login_as = (email, password)
        return email

    # -- low-level ----------------------------------------------------------
    async def _send(self, call, path: str, **kw) -> httpx.Response:
        # A run outlives the app's 30-minute access token: on a 401 log in
        # again, once, and retry once (SPEC-68). The report counts `relogin`.
        r = await self._paced(call, path, **kw)
        if r.status_code == 401 and self._login_as:
            self._token = await self._login(*self._login_as)
            self.relogins += 1
            r = await self._paced(call, path, **kw)
        return r

    async def _paced(self, call, path: str, **kw) -> httpx.Response:
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
        return await self._send(self._http.put, path, **kw)

    async def poll(self, path: str, done, *, timeout: float = 300, interval: float = 3):
        """Poll GET `path` until `done(json)` is truthy; returns that json.

        EVAL_POLL_SCALE stretches every wait, for a relay run where an agent
        answers each model call (evals/relay_llm.py) far slower than a vendor."""
        timeout *= float(os.getenv("EVAL_POLL_SCALE", "1"))
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
        # Characters are book-scoped (Canon model: book-as-root), so the API
        # 422s a create without book_id. Fail here, naming the cause, instead
        # of spending a request on a 422 mid-run (the attribution defect).
        if not book_id:
            raise EvalClientError(
                f"create character {name!r}: book_id is required (characters "
                "belong to a book)"
            )
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

    async def get_book(self, book_id: str) -> dict:
        r = await self._get(f"/api/v1/books/{book_id}")
        r.raise_for_status()
        return r.json()

    async def list_characters(self, book_id: str) -> list[dict]:
        r = await self._get("/api/v1/characters/", params={"book_id": book_id})
        r.raise_for_status()
        return r.json()["characters"]

    async def get_canon(self, book_id: str) -> dict:
        """{entries, style}: the book's Canon surface."""
        r = await self._get(f"/api/v1/books/{book_id}/canon")
        r.raise_for_status()
        return r.json()

    async def set_style(self, book_id: str, style: dict) -> dict:
        """Create or replace the book's style guide (pov, tense, tone, comps,
        sample_prose); keys starting with "_" (corpus comments) are dropped."""
        body = {k: v for k, v in style.items() if not k.startswith("_")}
        r = await self._put(f"/api/v1/books/{book_id}/canon/style", json=body)
        if r.status_code not in (200, 201):
            raise EvalClientError(f"set style failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def generate_outline(
        self,
        book_id: str,
        chapters_target: int = 8,
        timeout: float = 900,
        *,
        premise: dict | None = None,
    ) -> dict:
        """Generate an outline and return the finished plan.

        The staged generator runs as a background job and answers with a job
        handle, so poll the book's plans until the outline is no longer
        generating. (Reading `content` straight off the POST response is what
        pinned the outline eval at 0 after the staged generator shipped.)

        `premise` ({premise_restated, central_conflict}) is the author's edit:
        the run stops at the premise checkpoint and is confirmed WITH it, the
        "author-edited premise" path R6 grades. Without it the checkpoint (when
        the tier stops there) is confirmed as the model wrote it."""
        body: dict = {"kind": "outline", "chapters_target": chapters_target}
        if premise:
            body["confirm_premise"] = True
        r = await self._post(f"/api/v1/books/{book_id}/plans/generate", json=body)
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
            await self.confirm_premise(book_id, plan["id"], **(premise or {}))
            return await self.generate_outline_wait(book_id, timeout)
        return plan

    async def confirm_premise(
        self,
        book_id: str,
        plan_id: str,
        premise_restated: str | None = None,
        central_conflict: str | None = None,
    ) -> dict:
        """Confirm the restated premise; an edited field is authoritative."""
        body = {
            k: v
            for k, v in (
                ("premise_restated", premise_restated),
                ("central_conflict", central_conflict),
            )
            if v
        }
        r = await self._post(
            f"/api/v1/books/{book_id}/plans/{plan_id}/confirm-premise", json=body
        )
        if r.status_code != 200:
            raise EvalClientError(
                f"confirm premise failed ({r.status_code}): {r.text[:400]}"
            )
        return r.json()

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

    async def put_outline(self, book_id: str, content: list) -> dict:
        """Store an outline as the author's plan (PUT /books/{id}/plans): how a
        memoised outline reaches a fresh book without paying for it again."""
        r = await self._put(
            f"/api/v1/books/{book_id}/plans",
            json={"kind": "outline", "content": content},
        )
        if r.status_code not in (200, 201):
            raise EvalClientError(f"put plan failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def promote_all(self, book_id: str) -> dict:
        """Promote every outline node into a chapter: {chapters: [...], created}."""
        r = await self._post(f"/api/v1/books/{book_id}/plans/promote-all")
        if r.status_code != 200:
            raise EvalClientError(f"promote failed ({r.status_code}): {r.text[:400]}")
        return r.json()

    async def get_chapter(self, chapter_id: str) -> dict:
        """The chapter (summary, cast ids) and its scenes (id, status, position)."""
        r = await self._get(f"/api/v1/books/chapters/{chapter_id}")
        r.raise_for_status()
        return r.json()

    async def generate_planned(
        self, chapter_id: str, body: dict | None = None, timeout: float = 1800
    ) -> dict:
        """One scene per planned beat (POST .../scenes/generate-planned), then
        wait until the chain has written the scenes it queued (a deferred beat
        has none; SPEC-34), or stopped on a failure.

        Returns the start response plus `scenes`: the chapter's scenes in
        position order once settled."""
        r = await self._post(
            f"/api/v1/books/chapters/{chapter_id}/scenes/generate-planned",
            json=body or {},
        )
        if r.status_code != 200:
            raise EvalClientError(
                f"generate-planned failed ({r.status_code}): {r.text[:400]}"
            )
        started = r.json()
        want = started["skipped"] + started["queued"]
        streak = {"n": 0}

        def settled(ch: dict) -> bool:
            scenes = ch.get("scenes") or []
            terminal = all(
                s.get("status") in ("completed", "failed", "cancelled") for s in scenes
            )
            done = sum(1 for s in scenes if s.get("status") == "completed")
            stopped = any(s.get("status") in ("failed", "cancelled") for s in scenes)
            # The chain creates scene k+1 when scene k completes, so "all
            # terminal" must hold on two polls in a row to rule out that gap.
            ok = bool(scenes) and terminal and (done >= want or stopped)
            streak["n"] = streak["n"] + 1 if ok else 0
            return streak["n"] >= 2

        chapter = await self.poll(
            f"/api/v1/books/chapters/{chapter_id}", settled, timeout=timeout
        )
        started["scenes"] = sorted(
            chapter.get("scenes") or [], key=lambda s: s.get("position") or 0
        )
        return started

    async def usage(self) -> dict:
        """The author's usage (GET /usage): `tokens_used_24h` and the rest."""
        r = await self._get("/api/v1/usage")
        r.raise_for_status()
        return r.json()

    async def scene_grounding(self, scene_id: str) -> dict:
        """A scene's grounding record: per beat, samples and withheld lines."""
        r = await self._get(f"/api/v1/books/scenes/{scene_id}/grounding")
        r.raise_for_status()
        return r.json()

    async def get_scene(self, scene_id: str) -> dict:
        r = await self._get(f"/api/v1/scenes/{scene_id}")
        r.raise_for_status()
        return r.json()

    async def scene_beats(self, scene_id: str) -> list[dict]:
        """A scene's beats, [{beat_number, description, content}] in order, from
        `GET /scenes/{id}` (the API is the harness's only view of the app)."""
        return (await self.get_scene(scene_id)).get("beats") or []

    async def revise(self, scene_id: str, body: dict, timeout: float = 900) -> dict:
        """POST /books/scenes/{id}/revise (SPEC-19), wait for the job, and
        return {job, proposal, draft}: the proposal this job wrote (None when
        the job failed before writing one) and the draft it was written
        against."""
        r = await self._post(f"/api/v1/books/scenes/{scene_id}/revise", json=body)
        if r.status_code != 200:
            raise EvalClientError(f"revise failed ({r.status_code}): {r.text[:400]}")
        job_id = r.json()["job_id"]
        job = await self.poll(
            f"/api/v1/jobs/{job_id}",
            # A job ends "succeeded" (jobs table), "failed" (dead) or "cancelled".
            lambda j: j.get("status")
            in ("succeeded", "completed", "failed", "cancelled"),
            timeout=timeout,
        )
        listed = await self._get(f"/api/v1/books/scenes/{scene_id}/proposals")
        listed.raise_for_status()
        body = listed.json()
        proposal = next(
            (p for p in body.get("proposals") or [] if p.get("job_id") == job_id),
            None,
        )
        return {"job": job, "proposal": proposal, "draft": body.get("draft") or ""}

    async def revise_draft(self, scene_id: str) -> str:
        """The draft revise ranges are counted against (GET .../proposals)."""
        r = await self._get(f"/api/v1/books/scenes/{scene_id}/proposals")
        return (r.json().get("draft") or "") if r.status_code == 200 else ""

    async def accept_proposal(self, scene_id: str, revision_id: str) -> None:
        """POST .../accept: the proposal becomes the draft (409 if it moved)."""
        path = f"/api/v1/books/scenes/{scene_id}/revisions/{revision_id}/accept"
        r = await self._post(path)
        if r.status_code != 200:
            raise EvalClientError(f"accept failed ({r.status_code}): {r.text[:400]}")

    async def voice_chunks(
        self, character_id: str, chunk_type: str | None = None
    ) -> list[dict]:
        """A character's indexed chunks, of one type or all (the first 200)."""
        params = {"limit": 200, **({"chunk_type": chunk_type} if chunk_type else {})}
        r = await self._get(f"/api/v1/characters/{character_id}/chunks", params=params)
        r.raise_for_status()
        return r.json().get("chunks") or []

    async def discard_proposal(self, scene_id: str, revision_id: str) -> None:
        await self._post(
            f"/api/v1/books/scenes/{scene_id}/revisions/{revision_id}/discard"
        )

    async def delete_scene(self, scene_id: str) -> None:
        await self._send(self._http.delete, f"/api/v1/scenes/{scene_id}")

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
