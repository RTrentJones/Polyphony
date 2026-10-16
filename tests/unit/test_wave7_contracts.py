"""Wave-7 day-0 contracts (tpm-plan-wave7 rev 2, §1): the required sample
threshold, `tag_spans`, and the parsed-outline hash. Each is inert at day-0."""

import hashlib
import json
import types
import uuid

import pytest

import app.characters.context as ctx
import app.characters.dialogue as dialogue
from app.core.prompt_markers import ACT_CHAPTERS_HEADER
from app.parsing.character_extractor import (
    _attribute_paragraph,
    _normalize_quotes,
    tag_spans,
)
from evals.graders.plot import outline_hash
from evals.steps.pipeline import load_excerpts
from tests.unit.factories import make_scope

pytestmark = pytest.mark.unit


async def test_every_sample_caller_passes_the_threshold(monkeypatch):
    """(R) The two builders and the probe pass the setting to the store."""
    seen = []

    class Store:
        async def retrieve_similar(self, **kw):
            seen.append(kw["score_threshold"])
            return []

    class LLM:
        async def generate(self, messages, **kw):
            return types.SimpleNamespace(text="Fine.")

    monkeypatch.setattr(ctx, "get_chunk_store", lambda: Store())
    monkeypatch.setattr(ctx.settings, "RAG_SCORE_THRESHOLD", 0.123)
    monkeypatch.setattr(dialogue, "get_llm_client", lambda: LLM())
    milo = types.SimpleNamespace(
        id=uuid.uuid4(), name="Milo", aliases=[], user_id=None, book_id=None
    )
    scope = make_scope(cast=(milo,))
    await ctx.build_character_context(milo, "Milo", "beat", scope=scope)
    await ctx.build_agent_context(milo, "Milo", "beat", scope=scope)
    await dialogue.generate_dialogue(
        character=milo, prompt="Hi", context="", scope=scope
    )
    assert len(seen) >= 3 and set(seen) == {0.123}
    with pytest.raises(TypeError):  # required: no caller inherits a default
        await ctx.character_samples(milo, "beat", 3, scope=scope)


def test_tag_spans_are_the_tag_clauses():
    """(R) "Milo said" on cel p4, none on p11 (an action beat), "the Lich said"
    on p18; every clause lies outside the quoted speech."""
    paras = load_excerpts("cel").split("\n\n")
    assert [paras[4][a:b] for a, b in tag_spans(paras[4])] == ["Milo said"]
    assert tag_spans(paras[11]) == []
    assert [paras[18][a:b] for a, b in tag_spans(paras[18])] == ["the Lich said"]
    curly = "“Hi,” said Zara. Milo said: “Go.”"
    assert [curly[a:b] for a, b in tag_spans(curly)] == ["said Zara", "Milo said"]
    for book in ("cel", "dracula"):
        for p in load_excerpts(book).split("\n\n"):
            narration = _attribute_paragraph(_normalize_quotes(p.strip()))[1]
            assert all(p[a:b] in narration for a, b in tag_spans(p))


# sha256 of [(spans, narration), …] per paragraph at 399a85d, before tag_spans.
ATTRIBUTION = {
    "cel": "5f366bc28da6f2edf11a06f1b09b9879d1c1106604b3e5937bdd898c1203cda3",
    "dracula": "a6bbee2ee48c7012c3430ce5c0ac8ef6aaaefff038d3377c71f24eba7cbc74f6",
}


@pytest.mark.parametrize("book", sorted(ATTRIBUTION))
def test_the_speech_rule_is_unchanged_over_the_excerpts(book):
    """(R) `_attribute_paragraph`'s spans and narration, whole excerpt."""
    out = [
        _attribute_paragraph(_normalize_quotes(p.strip()))[:2]
        for p in load_excerpts(book).split("\n\n")
    ]
    digest = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    assert digest == ATTRIBUTION[book]


def _outline(answer: str) -> list[dict]:
    prompt = ACT_CHAPTERS_HEADER.format(number=1)
    return [
        {
            "purpose": "outline:beats",
            "messages": [{"content": prompt}],
            "answer": answer,
        }
    ]


def test_outline_hash_is_over_the_parsed_outline():
    """(F) Whitespace in the answer is ignored; a changed scene line is not."""
    scenes = [{"title": "Retained", "summary": "Milo meets Prava."}]
    answer = json.dumps([{"title": "One", "children": scenes}])
    spaced = json.dumps([{"title": "One", "children": scenes}], indent=4)
    assert outline_hash(_outline(answer)) == outline_hash(_outline(f"\n {spaced}\n"))
    changed = answer.replace("Prava", "Zara")
    assert outline_hash(_outline(changed)) != outline_hash(_outline(answer))
    assert len(outline_hash(_outline(answer))) == 64
