"""Wave-2 planned-beat contract (app/planning/planned_beat.py)."""

import uuid

import pytest

from app.planning.planned_beat import (
    LEGACY_MARKER,
    PlannedBeat,
    beat_from_line,
    beat_scene_description,
    normalize_planned_beats,
    split_legacy_summary,
    unwritten_beats,
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


# --- wave 2.5 day-0 (§1c, §1d) ---------------------------------------------------


def test_unwritten_beats_is_by_id_only():
    a, b, c = (normalize_planned_beats([{"title": t}])[0] for t in "ABC")
    beats = [a, b, c]
    assert unwritten_beats(beats, []) == beats
    assert unwritten_beats(beats, [None, b["node_id"], None]) == [a, c]
    # B3: renaming or re-summarising a beat never makes its scene unwritten
    renamed = {**a, "title": "A, renamed", "summary": "re-summarised"}
    assert unwritten_beats([renamed, b], {a["node_id"]}) == [b]
    assert unwritten_beats([], [a["node_id"]]) == []


def test_beat_from_line():
    assert beat_from_line("Intake: Milo wakes: late.") == {
        "title": "Intake",
        "summary": "Milo wakes: late.",
    }
    assert beat_from_line("Lunch:") == {"title": "Lunch", "summary": ""}
    assert beat_from_line("Lunch") == {"title": "Lunch", "summary": ""}
    line = "Intake: Milo wakes."
    assert beat_scene_description(beat_from_line(line)) == line


def _lines(result):
    return [(ln.head, ln.text) for ln in result.lines]


def test_split_promote_format_block():
    s = "Milo starts.\n\nPlanned beats:\n- Intake: Milo wakes.\n- Desk: He files."
    out = split_legacy_summary(s)
    assert out.text == "Milo starts."
    assert _lines(out) == [
        ("Intake: Milo wakes.", "Intake: Milo wakes."),
        ("Desk: He files.", "Desk: He files."),
    ]
    assert LEGACY_MARKER == "Planned beats:"


def test_split_block_at_the_start_of_the_summary():
    """An empty node summary made promote's .strip() put the block at 0."""
    out = split_legacy_summary("Planned beats:\n- Intake: Milo wakes.")
    assert out.text == ""
    assert _lines(out) == [("Intake: Milo wakes.", "Intake: Milo wakes.")]


def test_split_continuation_lines_join_the_previous_beat():
    """PR-S10: a child summary with newlines never de-plans the later beats."""
    s = (
        "Ch.\n\nPlanned beats:\n- Intake: Milo wakes.\n  He is late.\n"
        "And numb.\n- Desk: He files."
    )
    out = split_legacy_summary(s)
    assert _lines(out) == [
        ("Intake: Milo wakes.", "Intake: Milo wakes. He is late. And numb."),
        ("Desk: He files.", "Desk: He files."),
    ]


def test_split_keeps_text_after_the_block():
    s = "Ch.\n\nPlanned beats:\n- Intake: x\n\nAuthor's note.\n- not a beat"
    out = split_legacy_summary(s)
    assert out.text == "Ch.\n\nAuthor's note.\n- not a beat"
    assert _lines(out) == [("Intake: x", "Intake: x")]
    assert split_legacy_summary("Planned beats:\n- A: a\n\nNote.").text == "Note."


def test_split_drops_empty_beat_lines():
    out = split_legacy_summary("Ch.\n\nPlanned beats:\n- A: a\n- :\n- B:")
    assert _lines(out) == [("A: a", "A: a"), ("B:", "B:")]


@pytest.mark.parametrize(
    "summary",
    [
        None,
        "",
        "Milo starts.",
        "Planned beats: TBD. Then more.",
        "Ch.\n\nPlanned beats: TBD.\n- A: a",
        "Ch. Planned beats:\n- A: a",  # not on its own line
        "Ch.\nPlanned beats:\n- A: a",  # not after a blank line
        "Ch.\n\nPlanned beats:\nTBD\n- A: a",  # promote always wrote "- " first
        "Ch.\n\nPlanned beats:",
    ],
)
def test_split_is_none_without_a_promote_block(summary):
    assert split_legacy_summary(summary) is None
