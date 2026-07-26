"""Unit tests for the scene-generation workflow (plain async, no LangGraph)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.orchestration.workflow import (
    assemble_scene_text,
    generate_beat_dialogue,
    parse_beats,
)

SCENE_REQUEST = {
    "book_id": "00000000-0000-0000-0000-000000000001",
    "characters": ["Alice", "Bob"],
    "scene_description": "Two old friends argue about a hidden letter.",
    "setting": "A rain-soaked porch",
    "emotional_tone": "tense",
}


@pytest.mark.unit
class TestParseBeats:
    def test_parses_numbered_list(self):
        text = """1. Alice confronts Bob about the letter
2. Bob deflects with a joke
3. The truth comes out"""
        beats = parse_beats(text, ["Alice", "Bob"])
        assert len(beats) == 3
        assert beats[0]["description"] == "Alice confronts Bob about the letter"
        assert beats[0]["characters"] == ["Alice", "Bob"]

    def test_parses_dash_list(self):
        beats = parse_beats("- opening\n- middle", ["Alice"])
        assert len(beats) == 2

    def test_garbage_returns_empty(self):
        assert parse_beats("no structure here at all", ["Alice"]) == []


@pytest.mark.unit
class TestAssembleScene:
    def test_assembles_markdown_and_counts_words(self):
        beats = [
            {
                "description": "opening",
                "characters": ["Alice"],
                "dialogue": [
                    {
                        "character": "Alice",
                        "dialogue": "Where is the letter",
                        "action": "narrows her eyes",
                    }
                ],
            }
        ]
        text, words = assemble_scene_text(SCENE_REQUEST, beats)
        assert "**Alice**" in text
        assert "*narrows her eyes*" in text
        assert words == 4

    def test_empty_beats(self):
        text, words = assemble_scene_text(SCENE_REQUEST, [])
        assert words == 0
        assert "Generated Scene" in text


@pytest.mark.unit
class TestGenerateBeatDialogue:
    @pytest.mark.asyncio
    async def test_rotates_characters_and_collects_turns(self):
        beat = {"description": "the confrontation", "characters": ["Alice", "Bob"]}
        history_lengths = []

        async def fake(**kw):
            # Snapshot: the workflow passes the same (mutated) list each turn
            history_lengths.append(len(kw["previous_dialogue"]))
            return {
                "character": kw["character_name"],
                "dialogue": f"line from {kw['character_name']}",
                "action": "",
                "confidence_score": 0.9,
            }

        with patch("app.orchestration.workflow.generate_dialogue", fake):
            turns = await generate_beat_dialogue(
                beat, SCENE_REQUEST, {"Alice": "id-a", "Bob": "id-b"}
            )
        # 2 characters * 2 turns each, capped at 8
        assert len(turns) == 4
        assert turns[0]["character"] == "Alice"
        assert turns[1]["character"] == "Bob"
        # Previous dialogue grows one turn at a time
        assert history_lengths == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_failed_turn_is_skipped_not_fatal(self):
        beat = {"description": "beat", "characters": ["Alice"]}
        fake = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch("app.orchestration.workflow.generate_dialogue", fake):
            turns = await generate_beat_dialogue(beat, SCENE_REQUEST, {"Alice": "a"})
        assert turns == []
