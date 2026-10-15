"""SPEC-26 (W2-12): no duplicate bible in beat prompts, no doubled labels.

P8: the beat cast blocks carry name + voice samples + movement only; the canon
block (`canon.full_block()`) keeps the whole bible (BRD R1.1).
P9: `_render_json_field` drops a dict key equal to its label.
"""

from __future__ import annotations

import re
import types
import uuid

import pytest

import app.characters.context as ctx
from app.orchestration import prose as prose_module
from app.planning.canon import Canon, _render_json_field, render_character

pytestmark = pytest.mark.unit

_BIBLE_LINE = re.compile(r"^(Role|Description|Goals|Arc|Voice):", re.M)
_DOUBLED_VOICE = re.compile(r"(?i)\bvoice:\s*voice:")


def _char(name, description, voice=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        aliases=[],
        role="protagonist",
        description=description,
        goals=f"{name} wants out.",
        arc="learns to ask",
        notes="",
        personality_traits={"earnest": ""},
        relationships={},
        voice_characteristics=voice,
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
    )


class Store:
    def __init__(self, samples):
        self.samples = samples

    async def retrieve_similar(self, *, chunk_type=None, k=5, character_id=None, **kw):
        return [
            s
            for s in self.samples
            if s["cid"] == character_id and chunk_type in (None, s["chunk_type"])
        ][:k]


def _scope(cast):
    return ctx.SceneScope(tuple(cast), tuple(cast), (), frozenset(), "")


@pytest.fixture
def cast(monkeypatch):
    milo = _char("Milo Voss", "A new analyst, dead.", {"voice": "corporate, eager"})
    zara = _char("Zara Okafor", "A veteran clerk.", {"Voice": "dry"})
    store = Store(
        [
            {
                "id": "d1",
                "cid": str(milo.id),
                "text": "Let's circle back.",
                "chunk_type": "dialogue",
                "source": "dialogue_1",
            },
            {
                "id": "a1",
                "cid": str(zara.id),
                "text": "Zara Okafor did not look up.",
                "chunk_type": "action",
                "source": "paragraph_2",
            },
        ]
    )
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    return [milo, zara]


class TestRenderJsonField:
    def test_key_equal_to_label_is_dropped(self):
        assert _render_json_field({"voice": "dry"}, "Voice") == "dry"
        assert _render_json_field({"VOICE": "dry", "rhythm": "clipped"}, "Voice") == (
            "dry; rhythm: clipped"
        )

    def test_string_and_list_values_are_kept_as_written(self):
        # Review Could 3: only the dict key is dropped (SPEC-26 26e).
        assert _render_json_field("dry", "Voice") == "dry"
        assert _render_json_field(["dry", "terse"], "Voice") == "dry; terse"

    def test_without_label_unchanged(self):
        assert _render_json_field({"voice": "dry"}) == "voice: dry"
        assert _render_json_field({"wry": "", "calm": True}) == "wry; calm"

    def test_other_keys_keep_their_label(self):
        assert _render_json_field({"diction": "voice-y"}, "Voice") == "diction: voice-y"

    def test_canon_renders_voice_once(self):
        out = render_character(_char("Zara Okafor", "x", {"voice": "dry"}))
        assert "Voice: dry" in out.splitlines()
        assert not _DOUBLED_VOICE.search(out)


class TestCastContext:
    async def test_cast_context_has_no_bible(self, cast):
        out = await ctx.build_cast_context(cast, "the audit", scope=_scope(cast))
        assert not _BIBLE_LINE.search(out), out
        assert "### Milo Voss" in out and "### Zara Okafor" in out
        assert ctx.VOICE_SAMPLES_LABEL in out and "Let's circle back." in out
        assert "did not look up" in out
        assert (
            "How they move (gesture and habit only; do not reuse these events, "
            "objects or names):"
        ) in out.splitlines()
        assert out.count("<character>") == 2

    async def test_cast_context_bible_on_request(self, cast):
        out = await ctx.build_cast_context(
            cast, "the audit", scope=_scope(cast), bible=True
        )
        assert "Description: A new analyst, dead." in out
        assert "Voice: dry" in out and not _DOUBLED_VOICE.search(out)

    async def test_agent_context_keeps_the_bible(self, cast):
        out = await ctx.build_agent_context(
            cast[1], "Zara Okafor", "the audit", scope=_scope(cast)
        )
        assert "Description: A veteran clerk." in out
        assert "Goals: Zara Okafor wants out." in out
        assert "Voice: dry" in out.splitlines()
        assert not _DOUBLED_VOICE.search(out)

    async def test_character_context_defaults_to_no_bible(self, cast):
        out = await ctx.build_character_context(
            cast[0], "Milo Voss", "x", 0, scope=_scope(cast)
        )
        assert out == "### Milo Voss"


def test_scene_scope_of(cast):
    """SPEC-30: one scope per scene: the scene's rows, the book's, the request's
    secondaries and the canon's known names."""
    from app.planning.fidelity import canon_known_text

    edric = _char("Edric Thane", "A courier.")
    canon = Canon(title="Cel", synopsis="Milo dies.", characters=[*cast, edric])
    scope = ctx.SceneScope.of(canon, cast, ["Old Fen"])
    assert scope.cast == tuple(cast)
    assert scope.book_cast == (*cast, edric)
    assert scope.secondary_names == ("Old Fen",)
    assert scope.known == frozenset(canon.canon_terms()) and "Edric" in scope.known
    assert (
        scope.known_text == canon_known_text(canon) and "Milo dies." in scope.known_text
    )


class TestBeatPrompt:
    async def test_beat_prompt_carries_each_bible_once(self, cast, monkeypatch):
        prompts: list[str] = []

        class Fake:
            async def generate(self, messages, **kw):
                prompts.append(messages[0]["content"])
                return types.SimpleNamespace(text="Prose.", tokens_in=1, tokens_out=1)

        monkeypatch.setattr(prose_module, "get_llm_client", lambda: Fake())
        canon = Canon(title="Cel", synopsis="Milo dies.", characters=cast)
        cast_context = await ctx.build_cast_context(
            cast, "the audit", scope=_scope(cast)
        )
        await prose_module.write_beat_prose(
            {"description": "Milo finds the ledger.", "characters": ["Milo Voss"]},
            {"scene_description": "The audit."},
            cast_context,
            "",
            canon_block=canon.full_block(),
        )
        (prompt,) = prompts
        section = prompt.split("CHARACTERS IN THIS BEAT", 1)[1].split(
            "BEAT TO WRITE", 1
        )[0]
        assert not _BIBLE_LINE.search(section), section
        # The canon block is still whole: each Description exactly once.
        for c in cast:
            assert prompt.count(f"Description: {c.description}") == 1
            assert prompt.count(f"Goals: {c.goals}") == 1
        assert not _DOUBLED_VOICE.search(prompt)
