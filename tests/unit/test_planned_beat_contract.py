"""Wave-2 planned-beat contract (app/planning/planned_beat.py)."""

import uuid

import pytest

from app.planning.planned_beat import (
    PlannedBeat,
    beat_scene_description,
    normalize_planned_beats,
)

pytestmark = pytest.mark.unit


def test_scene_description_matches_the_legacy_line():
    assert beat_scene_description({"title": "Lunch", "summary": ""}) == "Lunch:"
    assert (
        beat_scene_description({"title": "Intake", "summary": "Milo wakes."})
        == "Intake: Milo wakes."
    )


def test_normalize_drops_untitled_beats_and_invalid_ids():
    cid = str(uuid.uuid4())
    out = normalize_planned_beats(
        [
            {"title": "  ", "summary": "no title"},
            "not a dict",
            {"title": "Intake", "node_id": "nope", "character_ids": [cid, "bad"]},
        ]
    )
    assert len(out) == 1
    assert out[0]["title"] == "Intake"
    assert uuid.UUID(out[0]["node_id"])  # replaced with a fresh uuid
    assert out[0]["character_ids"] == [cid]
    assert normalize_planned_beats(None) == []


def test_round_trip_through_the_model():
    beat = normalize_planned_beats(
        [{"title": "Desk", "secondary_names": ["Prava"], "tone": "dread"}]
    )[0]
    assert PlannedBeat(**beat).model_dump() == beat
