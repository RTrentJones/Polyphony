"""SPEC-11: voice workbench API, grounding provenance, character_chunks retired."""

import json
import pathlib
import re
import types
from unittest.mock import AsyncMock, patch

import pytest

from app.core.orm_models import Character, Scene, SceneBeat
from tests.unit.test_rag_store import make_store

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class PerCharacterStore:
    """Retrieval stub: one scored chunk per character, id derived from the id."""

    async def retrieve_similar(self, *, character_id, **kw):
        return [
            {
                "id": f"chunk-{character_id}",
                "text": "sample",
                "score": 0.5,
                "chunk_type": "dialogue",
            }
        ]


# --- chunk list API ---------------------------------------------------------


class TestChunkListStore:
    async def test_filters_and_paging_reach_the_sql(self):
        store, session = make_store(rows=[])
        await store.list_chunks(
            "char-1", skip=20, limit=10, source_id="src-1", chunk_type="dialogue"
        )
        sql, params = session.executed[-1]
        assert "source_id = :source_id" in sql and "chunk_type = :chunk_type" in sql
        assert "LIMIT :limit" in sql and "OFFSET :skip" in sql
        assert params == {
            "character_id": "char-1",
            "source_id": "src-1",
            "chunk_type": "dialogue",
            "limit": 10,
            "skip": 20,
        }

    async def test_count_uses_the_same_filters(self):
        store, session = make_store(rows=[(7,)])
        assert await store.count_chunks("char-1", chunk_type="action") == 7
        sql, params = session.executed[-1]
        assert "COUNT(*)" in sql and params["chunk_type"] == "action"

    async def test_get_chunks_keys_by_id(self):
        rows = [
            {
                "id": "c1",
                "character_id": "char-1",
                "source_id": None,
                "chunk_type": "dialogue",
                "text": "now",
                "source_location": "manual",
                "word_count": 1,
                "created_at": None,
            }
        ]
        store, _ = make_store(rows=rows)
        out = await store.get_chunks(["c1", "c1", None])
        assert out["c1"]["text"] == "now" and out["c1"]["character_id"] == "char-1"
        empty, session = make_store()
        assert await empty.get_chunks([]) == {}
        assert session.executed == []  # no query for nothing


class TestChunkListAPI:
    async def test_paging_and_filters_pass_through(
        self, client, auth_headers, test_character, test_source
    ):
        fake = AsyncMock()
        fake.list_chunks.return_value = [
            {
                "id": "c1",
                "source_id": str(test_source.id),
                "chunk_type": "dialogue",
                "text": "hi",
                "source": "",
                "word_count": 1,
                "created_at": "2026-10-12T00:00:00+00:00",
            }
        ]
        fake.count_chunks.return_value = 41
        cid = str(test_character.id)
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            r = await client.get(
                f"/api/v1/characters/{cid}/chunks",
                params={
                    "skip": 20,
                    "limit": 20,
                    "source_id": str(test_source.id),
                    "chunk_type": "dialogue",
                },
                headers=auth_headers,
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 41 and body["skip"] == 20 and body["limit"] == 20
        chunk = body["chunks"][0]
        assert {"id", "source_id", "created_at"} <= set(chunk)
        _, kw = fake.list_chunks.call_args
        assert kw == {
            "skip": 20,
            "limit": 20,
            "source_id": str(test_source.id),
            "chunk_type": "dialogue",
        }

    async def test_limit_is_bounded(self, client, auth_headers, test_character):
        r = await client.get(
            f"/api/v1/characters/{test_character.id}/chunks",
            params={"limit": 5000},
            headers=auth_headers,
        )
        assert r.status_code == 422

    async def test_edit_sends_new_text_to_reembed(
        self, client, auth_headers, test_character
    ):
        # Acceptance 1 (API half): the PATCH hands the NEW text to the store's
        # re-embedding update; the score change itself is pinned against real
        # pgvector in tests/integration/test_pg_pipeline.py.
        fake = AsyncMock()
        fake.update_chunk.return_value = True
        chunk_id = "00000000-0000-0000-0000-000000000001"
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            r = await client.patch(
                f"/api/v1/characters/{test_character.id}/chunks/{chunk_id}",
                json={"text": "Listen to them."},
                headers=auth_headers,
            )
        assert r.status_code == 200
        fake.update_chunk.assert_awaited_once_with(
            chunk_id, str(test_character.id), "Listen to them."
        )

    async def test_inspector_reports_threshold_and_skips_it(
        self, client, auth_headers, test_character
    ):
        from app.core.config import settings

        fake = AsyncMock()
        fake.retrieve_similar.return_value = []
        with patch("app.api.characters.get_chunk_store", return_value=fake):
            r = await client.post(
                f"/api/v1/characters/{test_character.id}/retrieve",
                json={"query": "how do they talk?", "k": 8},
                headers=auth_headers,
            )
        assert r.status_code == 200
        assert r.json()["threshold"] == settings.RAG_SCORE_THRESHOLD
        _, kw = fake.retrieve_similar.call_args
        # The inspector shows below-threshold hits too, with the line drawn.
        assert kw["score_threshold"] < 0 and kw["k"] == 8


# --- grounding record (acceptance 2) ------------------------------------------


@pytest.fixture
async def duo(async_session, test_book):
    milo = Character(book_id=test_book.id, user_id=test_book.user_id, name="Milo")
    zara = Character(book_id=test_book.id, user_id=test_book.user_id, name="Zara")
    async_session.add_all([milo, zara])
    await async_session.commit()
    return types.SimpleNamespace(book=test_book, milo=milo, zara=zara)


class _LLM:
    def __init__(self, responses):
        self.responses = responses

    async def generate(self, messages, *, purpose, **kw):
        return types.SimpleNamespace(
            text=self.responses.get(purpose, "{}"), tokens_in=1, tokens_out=1
        )


_PLAN = json.dumps(
    [
        {"description": "Both meet.", "characters": ["Milo", "Zara"]},
        {"description": "Milo alone.", "characters": ["Milo"]},
    ]
)


def _assert_grounded(scene, duo):
    grounding = scene.evaluation_scores["grounding"]
    assert set(grounding) == {"0", "1"}
    expected = {"0": {duo.milo.id, duo.zara.id}, "1": {duo.milo.id}}
    for beat, cast in expected.items():
        got = {e["character_id"] for e in grounding[beat]}
        assert got == {str(c) for c in cast}, beat
        for e in grounding[beat]:
            assert e["chunk_id"] == f"chunk-{e['character_id']}"
            assert e["score"] == 0.5


async def test_prose_records_grounding_for_every_beat_and_cast_member(
    async_session, duo, monkeypatch
):
    import app.characters.context as ctx_mod
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod
    from app.orchestration.prose import run_prose_scene_workflow

    llm = _LLM({"plan_beats": _PLAN, "beat_prose": "Prose. " * 20})
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: llm)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: llm)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: PerCharacterStore())

    scene = Scene(user_id=duo.book.user_id, book_id=duo.book.id, status="processing")
    async_session.add(scene)
    await async_session.commit()
    result = await run_prose_scene_workflow(
        scene.id,
        {
            "character_ids": [str(duo.milo.id), str(duo.zara.id)],
            "characters": [],
            "scene_description": "They meet.",
            "target_word_count": 200,
        },
        duo.book.user_id,
        book_id=duo.book.id,
    )
    assert result["status"] == "completed", result
    _assert_grounded(scene, duo)


async def test_ensemble_records_grounding_the_same_way(async_session, duo, monkeypatch):
    import app.characters.context as ctx_mod
    import app.core.database as db_mod
    import app.orchestration.ensemble as ens
    import app.orchestration.workflow as wf_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_TIER", "free")
    llm = _LLM(
        {
            "plan_beats": _PLAN,
            "ensemble:proposal": '{"intent":"i","actions":["nods"],"lines":["Hi"]}',
            "ensemble:narration": "Milo nods and Zara watches him. " * 3,
            "ensemble:review": '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":["Milo","Zara"],"objections":[]}',
        }
    )
    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(ens, "get_llm_client", lambda: llm)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: llm)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: PerCharacterStore())

    async def _cast(names, **kw):
        rows = {"Milo": duo.milo, "Zara": duo.zara}
        return {n: rows[n] for n in names if n in rows}

    monkeypatch.setattr(ens, "load_characters_for_book", _cast)

    scene = Scene(user_id=duo.book.user_id, book_id=duo.book.id, status="processing")
    async_session.add(scene)
    await async_session.commit()
    result = await ens.run_ensemble_scene_workflow(
        scene.id,
        {"characters": ["Milo", "Zara"], "scene_description": "They meet."},
        duo.book.user_id,
        book_id=duo.book.id,
    )
    assert result["status"] == "completed", result
    assert scene.evaluation_scores["mode"] == "ensemble"
    _assert_grounded(scene, duo)


# --- grounding endpoint (acceptance 3) ----------------------------------------


async def test_grounding_endpoint_resolves_live_and_marks_deleted(
    client, auth_headers, async_session, duo
):
    milo, zara = str(duo.milo.id), str(duo.zara.id)
    scene = Scene(
        user_id=duo.book.user_id,
        book_id=duo.book.id,
        status="completed",
        evaluation_scores={
            "mode": "prose",
            "grounding": {
                "0": [
                    {"character_id": milo, "chunk_id": "live-1", "score": 0.61},
                    {"character_id": zara, "chunk_id": "gone-1", "score": 0.33},
                ],
                "1": [{"character_id": milo, "chunk_id": "foreign", "score": 0.2}],
            },
        },
    )
    async_session.add(scene)
    await async_session.flush()
    async_session.add(SceneBeat(scene_id=scene.id, beat_number=0, description="Meet"))
    await async_session.commit()

    fake = AsyncMock()
    fake.get_chunks.return_value = {
        "live-1": {
            "id": "live-1",
            "character_id": milo,
            "text": "Edited since.",
            "chunk_type": "dialogue",
            "source": "manual",
        },
        # exists, but belongs to another character: not grounding for Milo
        "foreign": {
            "id": "foreign",
            "character_id": zara,
            "text": "x",
            "chunk_type": "dialogue",
            "source": "",
        },
    }
    with patch("app.api.grounding.get_chunk_store", return_value=fake):
        r = await client.get(
            f"/api/v1/books/scenes/{scene.id}/grounding", headers=auth_headers
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recorded"] is True and body["mode"] == "prose"
    first, second = body["beats"]
    assert first["beat_index"] == 0 and first["description"] == "Meet"
    by_char = {c["character_id"]: c for c in first["characters"]}
    assert by_char[milo]["name"] == "Milo"
    live = by_char[milo]["samples"][0]
    assert live == {
        "chunk_id": "live-1",
        "score": 0.61,
        "deleted": False,
        "text": "Edited since.",
        "chunk_type": "dialogue",
        "source": "manual",
    }
    gone = by_char[zara]["samples"][0]
    assert gone["deleted"] is True and gone["text"] is None
    assert second["characters"][0]["samples"][0]["deleted"] is True


async def test_grounding_endpoint_scene_without_record_and_ownership(
    client, auth_headers, async_session, test_book
):
    from app.core.orm_models import Book, User

    scene = Scene(user_id=test_book.user_id, book_id=test_book.id, status="completed")
    async_session.add(scene)
    other = User(email="other@example.com", hashed_password="x")
    async_session.add(other)
    await async_session.commit()
    other_book = Book(user_id=other.id, title="Theirs")
    async_session.add(other_book)
    await async_session.commit()
    foreign = Scene(user_id=other.id, book_id=other_book.id, status="completed")
    async_session.add(foreign)
    await async_session.commit()

    r = await client.get(
        f"/api/v1/books/scenes/{scene.id}/grounding", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == {
        "scene_id": str(scene.id),
        "mode": None,
        "recorded": False,
        "beats": [],
    }
    r = await client.get(
        f"/api/v1/books/scenes/{foreign.id}/grounding", headers=auth_headers
    )
    assert r.status_code == 404


# --- character_chunks retired (acceptance 4) ------------------------------------


def test_nothing_references_character_chunks():
    """Only the migrations that created and dropped the table may name it."""
    # `spec11_drop_character_chunks` is a revision id later migrations chain to.
    pattern = re.compile(r"CharacterChunk|(?<!drop_)character_chunks")
    allowed = {
        "alembic/versions/0001_book_as_root_baseline.py",
        "alembic/versions/0014_spec11_drop_character_chunks.py",
    }
    offenders = []
    for base in ("app", "alembic", "evals", "scripts", "frontend/lib"):
        for path in (REPO / base).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".sh"}:
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel in allowed or "node_modules" in rel:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(rel)
    assert offenders == []


def test_orm_has_no_character_chunks_table():
    import app.core.orm_models as orm
    from app.core.database import Base

    assert not hasattr(orm, "CharacterChunk")
    assert "character_chunks" not in Base.metadata.tables


async def test_voice_samples_write_only_voice_chunks(
    client, auth_headers, test_character
):
    fake = AsyncMock()
    fake.index_chunks.return_value = 2
    with patch("app.api.characters.get_chunk_store", return_value=fake):
        r = await client.post(
            f"/api/v1/characters/{test_character.id}/voice-samples",
            json={"samples": ["One.", "Two."], "chunk_type": "dialogue"},
            headers=auth_headers,
        )
    assert r.status_code == 200 and r.json()["indexed"] == 2
