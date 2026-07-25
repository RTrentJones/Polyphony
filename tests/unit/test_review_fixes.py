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
        cid = commit.json()["created"]["characters"][0]
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
    """#2: the synopsis is Canon — versioned and restorable."""

    async def test_edit_versions_and_restores(self, client, auth_headers, test_book):
        bid = str(test_book.id)
        # test_book already has a synopsis; edit it (v1)
        await client.patch(
            f"/api/v1/books/{bid}",
            json={"synopsis": "First version."},
            headers=auth_headers,
        )
        await client.patch(
            f"/api/v1/books/{bid}",
            json={"synopsis": "Second version."},
            headers=auth_headers,
        )
        versions = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}", headers=auth_headers
            )
        ).json()["versions"]
        assert [v["version_no"] for v in versions] == [2, 1]

        # restore v1 -> synopsis reverts
        rr = await client.post(
            f"/api/v1/books/{bid}/versions/synopsis/{bid}/restore/1",
            headers=auth_headers,
        )
        assert rr.status_code == 200, rr.text
        v3 = (
            await client.get(
                f"/api/v1/books/{bid}/versions/synopsis/{bid}/3", headers=auth_headers
            )
        ).json()
        assert v3["content"]["synopsis"] == "First version."


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


class TestReprocessNonDestructive:
    """#1: reprocessing a source never deletes existing (edited) canon."""

    async def test_existing_character_is_preserved(
        self, async_session, test_book, test_source, monkeypatch
    ):
        import app.parsing.pipeline as pipeline
        from app.core.orm_models import Character

        # an author's enriched character, already in the book
        existing = Character(
            user_id=test_book.user_id,
            book_id=test_book.id,
            source_id=test_source.id,
            name="Milo Voss",
            goals="quit the afterlife",  # hand-authored enrichment
        )
        async_session.add(existing)
        test_source.content_text = "Milo Voss and a new one, Zara Okafor, talk."
        await async_session.commit()
        existing_id = existing.id

        # extractor "re-finds" Milo (existing) + Zara (new); store is mocked
        monkeypatch.setattr(
            pipeline.char_extractor,
            "extract_characters",
            AsyncMock(return_value=["Milo Voss", "Zara Okafor"]),
        )
        monkeypatch.setattr(
            pipeline.char_extractor, "extract_character_content", lambda t, n: []
        )
        monkeypatch.setattr(
            pipeline.char_extractor,
            "get_character_statistics",
            lambda c: {"dialogue_count": 0},
        )

        class _Ctx:
            async def __aenter__(self_):
                return async_session

            async def __aexit__(self_, *a):
                return False

        monkeypatch.setattr(pipeline, "get_async_session", lambda: _Ctx(async_session))
        store = AsyncMock()
        store.index_chunks.return_value = 0
        monkeypatch.setattr(pipeline, "get_chunk_store", lambda: store)

        await pipeline.process_source(test_source.id, test_book.user_id, text=None)

        # Milo (edited) survives with goals intact; Zara was added
        preserved = await async_session.get(Character, existing_id)
        assert preserved is not None
        assert preserved.goals == "quit the afterlife"  # NOT clobbered
        store.delete_character.assert_not_called()  # nothing was deleted
