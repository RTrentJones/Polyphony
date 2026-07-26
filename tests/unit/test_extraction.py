"""Extraction: Source -> proposed Canon -> review -> commit (Phase 6)."""

import types

import pytest

from app.parsing.canon_extractor import _normalize_proposals

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


class TestNormalize:
    def test_coerces_and_drops_junk(self):
        raw = {
            "characters": [
                {"name": "Milo Voss", "role": "protagonist", "description": "dead"},
                {"name": "", "role": "x"},  # dropped (no name)
                "not a dict",  # dropped
            ],
            "canon_entries": [
                {"name": "Aeon", "category": "org", "content": "firm"},
                {
                    "name": "X",
                    "category": "bogus",
                    "content": "",
                },  # category -> concept
            ],
            "style": {"pov": "third", "tense": "past"},
            "synopsis": "  a synopsis  ",
        }
        out = _normalize_proposals(raw)
        assert [c["name"] for c in out["characters"]] == ["Milo Voss"]
        assert out["canon_entries"][1]["category"] == "concept"
        assert out["style"]["pov"] == "third"
        assert out["synopsis"] == "a synopsis"

    def test_missing_keys_are_safe(self):
        out = _normalize_proposals({})
        assert out == {
            "characters": [],
            "canon_entries": [],
            "style": {"pov": "", "tense": "", "tone": "", "comps": ""},
            "synopsis": "",
        }


class TestExtractEndpoint:
    async def test_enqueues_extraction_job(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from sqlalchemy import select

        from app.core.orm_models import Job

        test_source.content_text = "Milo Voss is a dead analyst at Aeon Holdings."
        await async_session.commit()

        r = await client.post(
            f"/api/v1/books/{test_book.id}/sources/{test_source.id}/extract",
            headers=auth_headers,
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "pending"
        run_id = body["run_id"]

        jobs = (
            (
                await async_session.execute(
                    select(Job).where(Job.kind == "extract_canon")
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].payload["run_id"] == run_id


class TestJobAndCommit:
    async def test_job_writes_proposals_then_commit_creates_canon(
        self, client, auth_headers, async_session, test_book, test_source, monkeypatch
    ):
        import app.jobs.handlers as handlers_mod
        import app.parsing.canon_extractor as extractor_mod

        test_source.content_text = "Milo Voss and Zara Okafor work for the CEL."
        await async_session.commit()

        # 1) start extraction (enqueues the job)
        run_id = (
            await client.post(
                f"/api/v1/books/{test_book.id}/sources/{test_source.id}/extract",
                headers=auth_headers,
            )
        ).json()["run_id"]

        # 2) run the job with a mocked LLM (bound to the test session)
        proposals_json = (
            '{"characters":[{"name":"Milo Voss","role":"protagonist","description":"analyst"},'
            '{"name":"Zara Okafor","role":"protagonist","description":"organizer"}],'
            '"canon_entries":[{"name":"Aeon Holdings","category":"org","content":"the firm"}],'
            '"style":{"pov":"third-limited","tense":"past"},'
            '"synopsis":"A corporate afterlife."}'
        )

        class FakeLLM:
            async def generate(self, *a, **k):
                return types.SimpleNamespace(
                    text=proposals_json, tokens_in=1, tokens_out=1
                )

        monkeypatch.setattr(extractor_mod, "get_llm_client", lambda: FakeLLM())
        monkeypatch.setattr(
            handlers_mod, "get_async_session", lambda: _Ctx(async_session)
        )
        await handlers_mod._run_extract_canon(
            {
                "run_id": run_id,
                "source_id": str(test_source.id),
                "user_id": str(test_book.user_id),
            }
        )

        # 3) review: proposals are ready
        run = (
            await client.get(
                f"/api/v1/books/{test_book.id}/extractions/{run_id}",
                headers=auth_headers,
            )
        ).json()
        assert run["status"] == "ready"
        assert len(run["proposals"]["characters"]) == 2

        # 4) commit the reviewed selection (drop Zara to prove review is honored)
        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run_id}/commit",
            json={
                "characters": [{"name": "Milo Voss", "role": "protagonist"}],
                "canon_entries": [{"name": "Aeon Holdings", "category": "org"}],
                "style": {"pov": "third-limited"},
                "synopsis": "A corporate afterlife.",
            },
            headers=auth_headers,
        )
        assert commit.status_code == 200, commit.text
        assert len(commit.json()["created"]["characters"]) == 1

        # the character exists, book-scoped, with an 'imported' version
        chars = (await client.get("/api/v1/characters/", headers=auth_headers)).json()[
            "characters"
        ]
        milo = next(c for c in chars if c["name"] == "Milo Voss")
        assert not any(c["name"] == "Zara Okafor" for c in chars)  # was not committed
        versions = (
            await client.get(
                f"/api/v1/books/{test_book.id}/versions/character/{milo['id']}",
                headers=auth_headers,
            )
        ).json()["versions"]
        assert versions[-1]["reason"] == "imported"

        canon = (
            await client.get(
                f"/api/v1/books/{test_book.id}/canon", headers=auth_headers
            )
        ).json()
        assert any(e["name"] == "Aeon Holdings" for e in canon["entries"])
        assert canon["style"]["pov"] == "third-limited"

    async def test_commit_merges_duplicate_names(
        self, client, auth_headers, test_book, test_source, async_session
    ):
        from app.core.orm_models import ExtractionRun

        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add(run)
        # pre-existing character with the same name
        cid = (
            await client.post(
                "/api/v1/characters/",
                json={"name": "Milo Voss", "book_id": str(test_book.id)},
                headers=auth_headers,
            )
        ).json()["id"]
        await async_session.commit()

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [{"name": "Milo Voss", "role": "protagonist"}]},
            headers=auth_headers,
        )
        assert commit.status_code == 200
        # MERGED (updated), not created and not 409'd (PR review #1)
        assert commit.json()["created"]["characters"] == []
        assert cid in commit.json()["created"]["updated_characters"]
