"""Unit tests for the offline direct-eval harness — mocked LLM, no key needed.

Verifies the wiring (pipeline fn → grader) and the deterministic beat_recall
proxy, so the fast local loop grades correctly the moment a real key is present.
"""

from unittest.mock import AsyncMock

import pytest

from evals import offline

pytestmark = pytest.mark.unit


class TestBeatRecall:
    BEATS = [
        {"title": "The prisoner in the castle", "kind": "inciting"},
        {"title": "A friend sickens by the sea", "kind": "rising"},
    ]

    def test_full_recall(self):
        nodes = [
            {"title": "Prisoner in the castle", "summary": "he is trapped"},
            {"title": "By the sea a friend sickens", "summary": "illness spreads"},
        ]
        assert offline.beat_recall(nodes, self.BEATS) == 1.0

    def test_partial_recall(self):
        nodes = [{"title": "The prisoner in the castle", "summary": ""}]
        assert offline.beat_recall(nodes, self.BEATS) == 0.5

    def test_children_count_toward_recall(self):
        nodes = [
            {
                "title": "X",
                "summary": "",
                "children": [
                    {"title": "prisoner castle", "summary": ""},
                    {"title": "friend sickens sea", "summary": ""},
                ],
            }
        ]
        assert offline.beat_recall(nodes, self.BEATS) == 1.0

    def test_no_beats_is_zero(self):
        assert offline.beat_recall([{"title": "x"}], []) == 0.0


async def test_offline_extraction_grades_prediction(monkeypatch):
    gt = {"cast": ["Nora Vance", "Verhoeven"], "book": "x"}
    monkeypatch.setattr(
        "app.parsing.character_extractor.CharacterExtractor.extract_characters",
        AsyncMock(return_value=["Nora Vance", "Verhoeven"]),
    )
    res = await offline.offline_extraction("corpus text", gt)
    assert res["score"] == 1.0 and res["f1"] == 1.0
    assert res["predicted"] == ["Nora Vance", "Verhoeven"]


async def test_offline_outline_scores_beat_recall(monkeypatch):
    gt = {
        "book": "x",
        "synopsis": "a tale",
        "canonical_beats": [
            {"title": "prisoner in the castle", "kind": "inciting"},
            {"title": "friend sickens", "kind": "rising"},
        ],
    }
    monkeypatch.setattr(
        "app.planning.outline.generate_outline",
        AsyncMock(
            return_value=[
                {"title": "The prisoner castle", "summary": "trapped"},
                {"title": "A friend sickens", "summary": "ill"},
            ]
        ),
    )
    res = await offline.offline_outline(gt)
    assert res["structural_ok"] is True
    assert res["beat_recall"] == 1.0 and res["score"] == 1.0

    # structural gate: empty outline scores 0 regardless of recall.
    monkeypatch.setattr(
        "app.planning.outline.generate_outline", AsyncMock(return_value=[])
    )
    res2 = await offline.offline_outline(gt)
    assert res2["score"] == 0.0
