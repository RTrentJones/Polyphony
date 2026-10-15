"""SPEC-17 (W3-5): "Test dialogue" grounds on the samples scenes use.

One retrieval (`context.character_samples`), one renderer
(`context.render_character`): the probe's prompt carries the same `### name`
block, own words only, as a beat prompt does."""

import types
import uuid

import pytest

import app.characters.context as ctx
import app.characters.dialogue as dialogue
from app.core.prompt_markers import VOICE_SAMPLES_LABEL
from app.planning.canon import Canon

pytestmark = pytest.mark.unit

LONG = ("I'd love to circle back on the low-hanging fruit, end to end. " * 6).strip()
LINES = [
    "So!",
    "Zara,",
    LONG,
    "Is there a dashboard or a tracker for that?",
    "Do you have a minute? I think I found a discrepancy.",
]
REPLY = "Totally, let's circle back on the numbers."


class Store:
    async def retrieve_similar(self, *, chunk_type=None, k=5, **kw):
        if chunk_type not in (None, "dialogue"):
            return []
        return [
            {"id": f"d{i}", "text": t, "chunk_type": "dialogue", "score": 0.9 - i / 10}
            for i, t in enumerate(LINES)
        ][:k]


class Embedder:
    """Scores 1.0 only against the WHOLE voice texts (no excerpts, no "So!")."""

    async def aencode(self, texts):
        full = {REPLY, *LINES[2:]}
        return [[1.0, 0.0] if t in full else [0.0, 1.0] for t in texts]


@pytest.fixture
def probe(monkeypatch):
    prompts: dict[str, str] = {}

    class LLM:
        async def generate(self, messages, *, purpose="", **kw):
            prompts[purpose] = messages[0]["content"]
            text = REPLY if purpose == "dialogue" else "He taps the desk."
            return types.SimpleNamespace(text=text)

    monkeypatch.setattr(ctx, "get_chunk_store", lambda: Store())
    monkeypatch.setattr(dialogue, "get_llm_client", lambda: LLM())
    monkeypatch.setattr(dialogue, "get_embedder", lambda: Embedder())
    milo = types.SimpleNamespace(
        id=uuid.uuid4(), name="Milo Voss", aliases=["Milo"], user_id=None, book_id=None
    )
    scope = ctx.SceneScope.of(Canon(title="Cel", characters=[milo]), [milo], ())
    return milo, scope, prompts


async def test_the_probe_uses_the_scene_samples(probe):
    milo, scope, prompts = probe
    out = await dialogue.generate_dialogue(
        character=milo,
        prompt="Ask about the numbers.",
        context="End of the quarter.",
        scope=scope,
    )
    prompt = prompts["dialogue"]
    assert "### Milo Voss" in prompt and VOICE_SAMPLES_LABEL in prompt
    for line in LINES[2:]:
        assert f'- "{line}"' in prompt
    assert '"So!"' not in prompt and '"Zara,"' not in prompt
    assert "Setting:" not in prompt and "neutral" not in prompt
    assert "End of the quarter." in prompt and "Ask about the numbers." in prompt
    # The action call sees the same block: one retrieval, two calls.
    assert "### Milo Voss" in prompts["action"] and LONG in prompts["action"]
    assert out["dialogue"] == REPLY and out["character"] == "Milo Voss"
    assert out["confidence_score"] == 1.0
    excerpts = [g["excerpt"] for g in out["grounding"]]
    assert len(excerpts) == 3 and excerpts[0].endswith("…")
    assert {g["character_id"] for g in out["grounding"]} == {str(milo.id)}
