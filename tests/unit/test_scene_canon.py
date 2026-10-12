"""SPEC-01: scene generation is canon-grounded and cast-by-ID.

Prose mode is the default scene generator and the only one the free tier
allows. These tests pin that every beat prompt carries the book's WHOLE canon
(synopsis tail, canon entries, style guide) fenced as story material, that the
cast is validated against the book at the API, and that the planner's per-beat
casting survives.
"""

import json
import types

import pytest

from app.core.llm_text import STORY_MATERIAL_NOTICE
from app.core.orm_models import CanonEntry, Chapter, Character, Scene, StyleGuide

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class FakeLLM:
    def __init__(self, plan_text):
        self.plan_text = plan_text
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
        text = self.plan_text if purpose == "plan_beats" else "Prose. " * 20
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)


class FakeStore:
    async def retrieve_similar(self, **kw):
        return [
            {
                "id": "chunk-1",
                "text": "I'll not be hurried.",
                "score": 0.4,
                "chunk_type": "dialogue",
            }
        ]


_TAIL = "THE-GLOSS: the CEL is the Chief Executive Lich."


@pytest.fixture
async def cel_book(async_session, test_book):
    test_book.synopsis = ("Spiritual attrition. " * 600) + _TAIL
    milo = Character(
        book_id=test_book.id,
        user_id=test_book.user_id,
        name="Milo Voss",
        role="protagonist",
        arc="numb to awake",
    )
    zara = Character(
        book_id=test_book.id,
        user_id=test_book.user_id,
        name="Zara Okafor",
        role="deuteragonist",
    )
    edric = Character(
        book_id=test_book.id,
        user_id=test_book.user_id,
        name="Edric Thane",
        role="antagonist",
    )
    async_session.add_all(
        [
            milo,
            zara,
            edric,
            CanonEntry(
                book_id=test_book.id,
                name="Aeon Holdings",
                category="org",
                content="The firm.",
            ),
            StyleGuide(book_id=test_book.id, pov="first person, Milo", tense="present"),
        ]
    )
    chapter = Chapter(
        book_id=test_book.id, title="One", position=0, summary="Milo's first day."
    )
    async_session.add(chapter)
    await async_session.commit()
    return types.SimpleNamespace(
        book=test_book, milo=milo, zara=zara, edric=edric, chapter=chapter
    )


def _bind(monkeypatch, async_session, fake):
    import app.characters.context as ctx_mod
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod

    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: FakeStore())


async def _run(async_session, cel, fake, monkeypatch, ids):
    from app.orchestration.prose import run_prose_scene_workflow

    _bind(monkeypatch, async_session, fake)
    scene = Scene(
        user_id=cel.book.user_id,
        chapter_id=cel.chapter.id,
        position=0,
        status="processing",
    )
    async_session.add(scene)
    await async_session.commit()
    request = {
        "character_ids": ids,
        "characters": [],
        "scene_description": "Milo meets Zara at the desk.",
        "setting": "Reconciliations",
        "emotional_tone": "wry",
        "target_word_count": 300,
    }
    result = await run_prose_scene_workflow(
        scene.id,
        request,
        cel.book.user_id,
        chapter_summary="Milo's first day.",
        book_id=cel.book.id,
    )
    return result, scene


async def test_beat_prompt_carries_whole_canon_fenced(
    async_session, cel_book, monkeypatch
):
    fake = FakeLLM(
        json.dumps(
            [{"description": "They meet.", "characters": ["Milo Voss", "Zara Okafor"]}]
        )
    )
    ids = [str(cel_book.milo.id), str(cel_book.zara.id)]
    result, _ = await _run(async_session, cel_book, fake, monkeypatch, ids)
    assert result["status"] == "completed", result
    for purpose in ("plan_beats", "beat_prose"):
        prompt = fake.prompts[purpose][0]
        assert prompt.startswith(STORY_MATERIAL_NOTICE)
        assert _TAIL in prompt, f"{purpose}: synopsis tail missing"
        assert "Aeon Holdings" in prompt
        assert "first person, Milo" in prompt  # the style guide
        assert "numb to awake" in prompt  # a full bible field
        # author/upload content is fenced
        assert "<synopsis>" in prompt and "<canon>" in prompt
    beat = fake.prompts["beat_prose"][0]
    assert "<character>" in beat and "I'll not be hurried." in beat
    assert "No bible entry" not in beat


async def test_per_beat_cast_is_preserved(async_session, cel_book, monkeypatch):
    fake = FakeLLM(
        json.dumps(
            [
                {"description": "Milo alone.", "characters": ["Milo Voss"]},
                {
                    "description": "Zara arrives.",
                    "characters": ["Zara Okafor", "Elara"],
                },
            ]
        )
    )
    ids = [str(cel_book.milo.id), str(cel_book.zara.id)]
    result, scene = await _run(async_session, cel_book, fake, monkeypatch, ids)
    assert result["status"] == "completed"
    first, second = fake.prompts["beat_prose"]

    def cast_part(p):
        return p.split("CHARACTERS IN THIS BEAT")[1].split("BEAT TO WRITE")[0]

    assert "### Milo Voss" in cast_part(first)
    assert "### Zara Okafor" not in cast_part(first)
    assert "### Zara Okafor" in cast_part(second)
    assert "Elara" not in cast_part(second)  # never invented
    # grounding provenance recorded per beat (R5.3)
    assert scene.evaluation_scores["grounding"]["0"][0]["chunk_id"] == "chunk-1"


class TestCastValidation:
    async def _setup(self, client, auth_headers):
        book = (
            await client.post(
                "/api/v1/books/", json={"title": "B"}, headers=auth_headers
            )
        ).json()
        chapter = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C"},
                headers=auth_headers,
            )
        ).json()
        milo = (
            await client.post(
                "/api/v1/characters/",
                json={"name": "Milo Voss", "book_id": book["id"]},
                headers=auth_headers,
            )
        ).json()
        return book, chapter, milo

    def _body(self, **cast):
        return {
            "scene_description": "A quiet moment at the desk.",
            "setting": "office",
            "emotional_tone": "wry",
            **cast,
        }

    async def test_unknown_name_is_rejected_and_nothing_enqueued(
        self, client, auth_headers, async_session
    ):
        from sqlalchemy import func, select

        from app.core.orm_models import Job

        _book, chapter, _milo = await self._setup(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{chapter['id']}/scenes/generate",
            json=self._body(characters=["Milo Voss", "Elara"]),
            headers=auth_headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["unknown"] == ["Elara"]
        n_jobs = (await async_session.execute(select(func.count(Job.id)))).scalar()
        n_scenes = (await async_session.execute(select(func.count(Scene.id)))).scalar()
        assert n_jobs == 0 and n_scenes == 0

    async def test_character_from_another_book_is_rejected(self, client, auth_headers):
        _b, chapter, _m = await self._setup(client, auth_headers)
        _b2, _c2, other = await self._setup(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{chapter['id']}/scenes/generate",
            json=self._body(character_ids=[other["id"]]),
            headers=auth_headers,
        )
        assert r.status_code == 422

    async def test_names_resolve_case_insensitively_to_ids(
        self, client, auth_headers, async_session
    ):
        _b, chapter, milo = await self._setup(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{chapter['id']}/scenes/generate",
            json=self._body(characters=["milo voss"]),
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        scene = await async_session.get(
            Scene, __import__("uuid").UUID(r.json()["scene_id"])
        )
        assert scene.character_ids == [milo["id"]]
        assert scene.characters == ["Milo Voss"]


def test_no_string_slices_of_author_content_in_scene_paths():
    """A silent cut is what kept the Elara defect invisible (BRD R2.2)."""
    import pathlib
    import re

    for path in (
        "app/orchestration/prose.py",
        "app/orchestration/workflow.py",
        "app/characters/context.py",
    ):
        for line in pathlib.Path(path).read_text().splitlines():
            if "hexdigest()" in line:  # a hash prefix, not author content
                continue
            assert not re.search(r"\[:\s*\d+\s*\]", line), f"{path}: {line}"
