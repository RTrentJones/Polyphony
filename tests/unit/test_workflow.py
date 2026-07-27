"""Unit tests for scene beat planning (the dialogue-turn generator was removed)."""

import pytest

from app.orchestration.workflow import parse_beats


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
