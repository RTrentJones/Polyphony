"""Regression tests for the PR #19 review findings (legacy paths bypassing the
new invariants). Each test name cites the finding number."""

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings

pytestmark = pytest.mark.unit


class TestFullImportedSnapshot:
    """#3: extraction-committed characters snapshot ALL authored fields."""

    async def test_imported_version_is_full_state(
        self, client, auth_headers, async_session, test_book, test_source
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
        await async_session.commit()

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [{"name": "Milo", "role": "protagonist"}]},
            headers=auth_headers,
        )
        cid = commit.json()["result"]["characters"]["created"][0]
        v1 = (
            await client.get(
                f"/api/v1/books/{test_book.id}/versions/character/{cid}/1",
                headers=auth_headers,
            )
        ).json()
        # full snapshot: enrichment fields present (as None), not omitted
        for f in ("goals", "arc", "relationships", "notes", "voice_characteristics"):
            assert f in v1["content"], f


class TestSynopsisVersioning:
    """#2: the synopsis is Canon — create -> v1, edit -> v2, original recoverable."""

    async def test_create_v1_edit_v2_restore(self, client, auth_headers):
        book = (
            await client.post(
                "/api/v1/books/",
                json={"title": "B", "synopsis": "Version A."},
                headers=auth_headers,
            )
        ).json()
        bid = book["id"]
        await client.patch(
            f"/api/v1/books/{bid}",
            json={"synopsis": "Version B."},
            headers=auth_headers,
        )
        versions = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}", headers=auth_headers
            )
        ).json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]  # create->v1, edit->v2
        v1 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}/1", headers=auth_headers
            )
        ).json()
        assert v1["content"]["synopsis"] == "Version A."  # the ORIGINAL is kept

        # restore v1 -> synopsis reverts to A
        await client.post(
            f"/api/v1/books/{bid}/versions/synopsis/{bid}/restore/1",
            headers=auth_headers,
        )
        v3 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}/3", headers=auth_headers
            )
        ).json()
        assert v3["content"]["synopsis"] == "Version A."

    async def test_preexisting_unversioned_synopsis_is_preserved(
        self, client, auth_headers, test_book
    ):
        # test_book's fixture set a synopsis directly (unversioned). The first edit
        # must preserve that original as v1 before writing the new value.
        bid = str(test_book.id)
        await client.patch(
            f"/api/v1/books/{bid}", json={"synopsis": "Edited."}, headers=auth_headers
        )
        v1 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}/1", headers=auth_headers
            )
        ).json()
        assert v1["content"]["synopsis"] == "A test synopsis."  # not lost


class TestDeletedCanonRestore:
    """#4: deleting canon is no longer permanent — restore recreates it."""

    async def test_deleted_character_can_be_restored(
        self, client, auth_headers, test_book
    ):
        bid = str(test_book.id)
        cid = (
            await client.post(
                "/api/v1/characters/",
                json={"name": "Zara", "book_id": bid, "role": "protagonist"},
                headers=auth_headers,
            )
        ).json()["id"]

        # delete it (best-effort vector cleanup mocked away)
        fake = AsyncMock()
        fake.delete_character.return_value = None
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            d = await client.delete(f"/api/v1/characters/{cid}", headers=auth_headers)
        assert d.status_code == 204

        # restore v1 -> the character comes back with the SAME id
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/character/{cid}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text
        chars = (await client.get("/api/v1/characters/", headers=auth_headers)).json()[
            "characters"
        ]
        restored = [c for c in chars if c["id"] == cid]
        assert restored and restored[0]["name"] == "Zara"

    async def test_deleted_canon_entry_can_be_restored(
        self, client, auth_headers, test_book
    ):
        bid = str(test_book.id)
        eid = (
            await client.post(
                f"/api/v1/books/{bid}/canon/entries",
                json={"name": "Aeon", "category": "org", "content": "the firm"},
                headers=auth_headers,
            )
        ).json()["id"]
        await client.delete(
            f"/api/v1/books/{bid}/canon/entries/{eid}", headers=auth_headers
        )
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/canon_entry/{eid}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text
        entries = (
            await client.get(f"/api/v1/books/{bid}/canon", headers=auth_headers)
        ).json()["entries"]
        assert any(e["id"] == eid and e["name"] == "Aeon" for e in entries)


class TestEnsembleFullCast:
    """#6: characters over the agent cap are still validated, not dropped."""

    async def test_context_only_characters_are_validated(self, monkeypatch):
        from app.orchestration import ensemble as ens

        monkeypatch.setattr(settings, "LLM_TIER", "free")  # max_agents == 3
        fake_responses = {
            "ensemble:proposal": '{"intent":"i","actions":["nods"],"lines":[]}',
            "ensemble:narration": "Some prose about the scene.",
            # editor names NOBODY acting -> every present char is a blocking objection
            "ensemble:review": '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":[],"objections":[]}',
        }

        class FakeLLM:
            calls: list[str] = []

            async def generate(self, m, *, purpose, **kw):
                FakeLLM.calls.append(purpose)
                return types.SimpleNamespace(text=fake_responses[purpose])

        monkeypatch.setattr(ens, "get_llm_client", lambda: FakeLLM())

        async def _no_chars(names, **kw):
            return {}

        monkeypatch.setattr(ens, "load_characters_for_book", _no_chars)

        out = await ens.run_ensemble(
            scene_request={"characters": ["A", "B", "C", "D", "E"], "setting": "x"},
            beat_description="a crowd scene",
            user_id=None,
            book_id=None,
            canon_context="",
        )
        # only 3 propose, but the editor validates ALL FIVE (D and E, over the cap,
        # are flagged rather than silently dropped)
        assert FakeLLM.calls.count("ensemble:proposal") == 3
        flagged = {o["character"] for o in out["evaluation"]["objections"]}
        assert {"D", "E"} <= flagged


class TestUploadRoutesThroughReview:
    """#1: upload/reprocess propose via ExtractionRun; commit MERGES, never drops
    reviewed fields onto an existing name-only character."""

    async def test_commit_merges_into_existing_name_only_character(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import Character, ExtractionRun

        # a name-only character (as a prior commit / seed might create)
        existing = Character(
            user_id=test_book.user_id, book_id=test_book.id, name="Milo Voss"
        )
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add_all([existing, run])
        await async_session.commit()
        cid = str(existing.id)

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={
                "characters": [
                    {
                        "name": "Milo Voss",
                        "role": "protagonist",
                        "description": "a dead analyst",
                    }
                ]
            },
            headers=auth_headers,
        )
        assert commit.status_code == 200, commit.text
        # merged (updated), not silently skipped
        assert cid in commit.json()["result"]["characters"]["updated"]
        assert commit.json()["result"]["characters"]["created"] == []

        # the reviewed role + description were applied AND versioned
        v1 = (
            await client.get(
                f"/api/v1/books/{test_book.id}/versions/character/{cid}/1",
                headers=auth_headers,
            )
        ).json()
        assert v1["content"]["role"] == "protagonist"
        assert v1["content"]["description"] == "a dead analyst"

        # and the merged character (source_id=None) is enqueued for voice indexing
        # by EXPLICIT id — not rediscovered via Character.source_id (PR review #2).
        from sqlalchemy import select

        from app.core.orm_models import Job

        job = (
            await async_session.execute(
                select(Job).where(Job.kind == "index_characters_voice")
            )
        ).scalar_one()
        assert cid in job.payload["character_ids"]


class TestCommitContractHonored:
    """Round 5 #1: the review screen's contract is consistent — an approved
    existing canon entry / synopsis is MERGED, never silently dropped."""

    async def test_existing_canon_entry_edit_is_applied(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import CanonEntry, ExtractionRun

        entry = CanonEntry(
            book_id=test_book.id, name="Aeon Holdings", category="org", content="old"
        )
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add_all([entry, run])
        await async_session.commit()
        eid = str(entry.id)

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={
                "canon_entries": [
                    {
                        "name": "Aeon Holdings",
                        "category": "faction",
                        "content": "the new firm",
                    }
                ]
            },
            headers=auth_headers,
        )
        assert commit.status_code == 200, commit.text
        # applied as an UPDATE, not silently skipped
        assert eid in commit.json()["result"]["canon_entries"]["updated"]
        assert commit.json()["result"]["canon_entries"]["created"] == []

        canon = (
            await client.get(
                f"/api/v1/books/{test_book.id}/canon", headers=auth_headers
            )
        ).json()
        row = next(e for e in canon["entries"] if e["id"] == eid)
        assert row["content"] == "the new firm"
        assert row["category"] == "faction"

    async def test_existing_synopsis_edit_is_applied(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import ExtractionRun

        # test_book's fixture already set a synopsis. Committing a new one must
        # APPLY (the old code only wrote when the book had none) and be versioned.
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add(run)
        await async_session.commit()

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"synopsis": "A corporate afterlife, revised."},
            headers=auth_headers,
        )
        assert commit.status_code == 200, commit.text
        assert commit.json()["result"]["synopsis"] == "updated"
        book = (
            await client.get(f"/api/v1/books/{test_book.id}", headers=auth_headers)
        ).json()
        assert book["synopsis"] == "A corporate afterlife, revised."


class TestSourceCastReachable:
    """Round 5 #2: a merged/multi-source character (source_id != this source) is
    still reachable from the source, via the source_characters association."""

    async def test_merged_character_appears_in_source_cast(
        self, client, auth_headers, async_session, test_book, test_source
    ):
        from app.core.orm_models import Character, ExtractionRun

        # An existing character with NO originating source (manual, or older file).
        existing = Character(
            user_id=test_book.user_id, book_id=test_book.id, name="Milo Voss"
        )
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add_all([existing, run])
        await async_session.commit()
        cid = str(existing.id)
        assert existing.source_id is None  # merged, not owned by this source

        commit = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [{"name": "Milo Voss", "role": "protagonist"}]},
            headers=auth_headers,
        )
        assert commit.status_code == 200, commit.text

        # reachable from the source despite source_id staying NULL (PR review #2)
        chars = (
            await client.get(
                f"/api/v1/sources/{test_source.id}/characters", headers=auth_headers
            )
        ).json()["characters"]
        assert any(c["id"] == cid for c in chars)


class TestExtractionResumable:
    """Round 5 #3: the source exposes its latest extraction run so a review that
    was navigated away from can be resumed, not stranded."""

    async def test_get_source_exposes_latest_extraction(
        self, client, auth_headers, async_session, test_book, test_source
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
        await async_session.commit()

        src = (
            await client.get(f"/api/v1/sources/{test_source.id}", headers=auth_headers)
        ).json()
        assert src["latest_extraction"]["id"] == str(run.id)
        assert src["latest_extraction"]["status"] == "ready"


class TestVoiceIndexingRetryable:
    """#3: voice indexing retries incomplete (indexed_at IS NULL) characters."""

    async def test_failed_index_is_retried_not_skipped(
        self, async_session, test_book, test_source, monkeypatch
    ):
        import app.parsing.pipeline as pipeline
        from app.core.orm_models import Character

        char = Character(
            user_id=test_book.user_id,
            book_id=test_book.id,
            source_id=test_source.id,
            name="Mina",
        )  # indexed_at is NULL
        test_source.content_text = "Mina speaks."
        async_session.add(char)
        await async_session.commit()
        cid = char.id

        monkeypatch.setattr(
            pipeline.char_extractor,
            "extract_character_content",
            lambda t, n: [{"chunk_type": "dialogue", "text": "The dead travel fast."}],
        )
        monkeypatch.setattr(
            pipeline.char_extractor,
            "get_character_statistics",
            lambda c: {"dialogue_count": 1},
        )

        class _Ctx:
            async def __aenter__(self_):
                return async_session

            async def __aexit__(self_, exc_type, *a):
                # Mirror the real session factory: commit on clean exit.
                if exc_type is None:
                    await async_session.commit()
                return False

        monkeypatch.setattr(pipeline, "get_async_session", lambda: _Ctx())

        store = AsyncMock()
        store.index_chunks.side_effect = RuntimeError("vector store down")
        monkeypatch.setattr(pipeline, "get_chunk_store", lambda: store)

        # first attempt fails at index_chunks -> raises (job would retry)
        with pytest.raises(RuntimeError):
            await pipeline.index_characters_voice(
                test_source.id, test_book.id, test_book.user_id, [str(cid)]
            )
        # the character is STILL unindexed, so a retry will re-process it
        await async_session.refresh(char)
        assert char.indexed_at is None

        # retry succeeds
        store.index_chunks.side_effect = None
        store.index_chunks.return_value = 1
        await pipeline.index_characters_voice(
            test_source.id, test_book.id, test_book.user_id, [str(cid)]
        )
        await async_session.refresh(char)
        assert char.indexed_at is not None  # now complete
        assert cid == char.id
