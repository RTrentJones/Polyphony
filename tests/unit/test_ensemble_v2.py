"""SPEC-14: ensemble v2 — split voice/physicality grounding, chapter intent,
no silent slices, preflight, and the ensemble-vs-prose grader."""

import types
import uuid

import pytest

from app.core.config import settings
from app.orchestration import ensemble as ens
from tests.unit.factories import make_scope

_SCOPE = make_scope()

pytestmark = pytest.mark.unit


class FakeLLM:
    def __init__(self):
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
        text = {
            "ensemble:proposal": '{"intent":"x","actions":["leans in"],"lines":["Hi"]}',
            "ensemble:narration": "Milo leaned in.",
        }.get(purpose, "{}")
        return types.SimpleNamespace(text=text, tokens_in=1, tokens_out=1)


class TypedStore:
    """Returns samples by chunk_type, like the real store's filter."""

    def __init__(self):
        self.samples = {
            "dialogue": [
                {
                    "id": "d1",
                    "text": "I'll not be hurried.",
                    "score": 0.4,
                    "chunk_type": "dialogue",
                },
                {
                    "id": "d2",
                    "text": "Read the appendices.",
                    "score": 0.3,
                    "chunk_type": "dialogue",
                },
            ],
            "action": [
                {
                    "id": "a1",
                    "text": "He straightened the ledger twice.",
                    "score": 0.4,
                    "chunk_type": "action",
                },
                {
                    "id": "a2",
                    "text": "He did not sit.",
                    "score": 0.35,
                    "chunk_type": "action",
                },
            ],
        }

    async def retrieve_similar(self, *, chunk_type=None, k=5, keep=None, **kw):
        if chunk_type is None:
            every = [*self.samples["dialogue"], *self.samples["action"]]
            rows = [*every, *self.samples.get("unknown", [])][:k]
        else:
            rows = list(self.samples.get(chunk_type, []))[:k]
        return [r for r in rows if keep is None or keep(r)]  # the store's `keep`


def _character():
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name="Milo Voss",
        role="protagonist",
        description="",
        goals="",
        arc="",
        voice_characteristics=None,
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
    )


async def test_agent_gets_voice_and_physicality_retrieved_separately(monkeypatch):
    import app.characters.context as ctx

    fake = FakeLLM()
    monkeypatch.setattr(ens, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: TypedStore())
    grounding: list = []
    await ens.propose(
        _character(),
        "Milo Voss",
        "the desk",
        [],
        None,
        scope=_SCOPE,
        grounding=grounding,
    )
    prompt = fake.prompts["ensemble:proposal"][0]
    voice = prompt.split("<voice>")[1].split("</voice>")[0]
    body = prompt.split("<physicality>")[1].split("</physicality>")[0]
    assert "I'll not be hurried." in voice and "ledger" not in voice
    assert "straightened the ledger" in body and "hurried" not in body
    assert {g["chunk_id"] for g in grounding} == {"d1", "d2", "a1", "a2"}


async def test_physicality_falls_back_when_untyped(monkeypatch):
    import app.characters.context as ctx

    store = TypedStore()
    store.samples["action"] = []  # the classifier under-labels
    store.samples["unknown"] = [
        {
            "id": "u1",
            "text": "He hummed while filing.",
            "score": 0.3,
            "chunk_type": "unknown",
        }
    ]
    fake = FakeLLM()
    monkeypatch.setattr(ens, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    await ens.propose(_character(), "Milo Voss", "the desk", [], None, scope=_SCOPE)
    prompt = fake.prompts["ensemble:proposal"][0]
    block = prompt.split("<physicality>\n")[1].split("\n</physicality>")[0]
    assert "He hummed while filing." in block  # untyped fallback fills it
    assert "hurried" not in block  # never repeats the voice samples


async def test_narrator_gets_chapter_intent_and_whole_tail(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(ens, "get_llm_client", lambda: fake)
    tail = ("word " * 400) + "TAIL-END"
    await ens.narrate(
        [
            {
                "character": "Milo",
                "intent": "",
                "actions": ["sits"],
                "lines": [],
                "interiority": "",
            }
        ],
        {"setting": "office", "target_word_count": 300},
        "<canon>c</canon>",
        tail,
        None,
        chapter_summary="CHAPTER-INTENT: Milo's first day.",
    )
    prompt = fake.prompts["ensemble:narration"][0]
    assert "CHAPTER-INTENT" in prompt and "<chapter>" in prompt
    assert "TAIL-END" in prompt and "<prior>" in prompt  # no 600-char cut


def test_no_slices_of_author_content_in_ensemble():
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("app/orchestration/ensemble.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            src = ast.unparse(node)
            assert "tail" not in src and "canon" not in src, src


async def test_ensemble_request_refused_when_requests_cannot_cover_it(
    client, auth_headers, monkeypatch
):
    from app.core import estimates

    monkeypatch.setattr(settings, "LLM_TIER", "paid")

    async def three_left():
        return 3

    monkeypatch.setattr(estimates, "requests_left_today", three_left)
    book = (
        await client.post("/api/v1/books/", json={"title": "B"}, headers=auth_headers)
    ).json()
    ch = (
        await client.post(
            f"/api/v1/books/{book['id']}/chapters",
            json={"title": "C"},
            headers=auth_headers,
        )
    ).json()
    c = (
        await client.post(
            "/api/v1/characters/",
            json={"name": "Milo", "book_id": book["id"]},
            headers=auth_headers,
        )
    ).json()
    body = {
        "character_ids": [c["id"]],
        "scene_description": "A quiet moment here.",
        "setting": "office",
        "emotional_tone": "wry",
    }
    r = await client.post(
        f"/api/v1/books/chapters/{ch['id']}/scenes/generate",
        json={**body, "mode": "ensemble"},
        headers=auth_headers,
    )
    assert r.status_code == 429
    assert r.json()["detail"]["estimate"]["mode"] == "ensemble"
    # prose is allowed (it would pause and resume instead)
    r = await client.post(
        f"/api/v1/books/chapters/{ch['id']}/scenes/generate",
        json={**body, "mode": "prose"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


def test_action_coverage_counts_narrated_action_not_speech_tags():
    from evals.graders.ensemble import action_coverage

    cast = ["Milo Voss", "Zara Okafor"]
    assert action_coverage('"Go," Milo said. Zara crossed the room.', cast) == 0.5
    assert action_coverage("Milo leaned in. Zara laughed.", cast) == 1.0
    assert action_coverage("", cast) == 0.0
