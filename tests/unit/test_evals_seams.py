"""The seam grader counts text repeated where two generated pieces meet."""

import pytest

from evals.graders import seams

pytestmark = pytest.mark.unit

BEAT_1 = (
    "Milo initialled the form and slid it across the partition. Zara did not "
    "look up. The coffee station hissed somewhere behind them."
)
BEAT_2_CLEAN = (
    "By the afternoon the queue had doubled. Milo closed four accounts before "
    "he noticed that one of them had no name at all."
)
# Beat 2 re-tells the last sentence of beat 1 (curly quote, different case).
BEAT_2_COPY = (
    "zara did not look up. The coffee station hissed somewhere behind them. "
    "Then Milo opened the ledger."
)


def test_a_copied_sentence_overlaps():
    assert seams.max_overlap([BEAT_1, BEAT_2_COPY]) >= 1


def test_clean_seams_do_not_overlap():
    assert seams.max_overlap([BEAT_1, BEAT_2_CLEAN]) == 0
    assert seams.max_overlap([BEAT_1]) == 0
    assert seams.max_overlap([]) == 0


def test_only_adjacent_pieces_are_compared():
    # The repeat is between pieces 0 and 2, which never meet.
    assert seams.max_overlap([BEAT_1, BEAT_2_CLEAN, BEAT_2_COPY]) == 0


def test_punctuation_and_case_do_not_hide_a_repeat():
    a = "He said, “Read the appendices first. Every one of them.”"
    b = "READ THE APPENDICES FIRST -- every one of them, she had said."
    assert seams.max_overlap([a, b]) >= 1
    assert seams.words("Don’t") == ["don't"]


def test_the_report_names_the_dirty_seam():
    report = seams.seam_report([BEAT_1, BEAT_2_CLEAN, BEAT_2_COPY, BEAT_1])
    assert report["seams"] == 3
    assert [p["shared"] > 0 for p in report["pairs"]] == [False, False, True]
    assert report["dirty_seams"] == 1
    assert report["max_overlap"] == report["pairs"][2]["shared"]
    assert report["pairs"][2]["example"]


def _filler(tag: str) -> str:
    """More than SEAM_WORDS distinct words, so a phrase can sit well away
    from the seam."""
    return " ".join(f"{tag}{i}" for i in range(seams.SEAM_WORDS + 10))


_NAME = "Edric Thane, the Chief Executive Lich"


def test_a_name_repeated_mid_text_is_not_a_seam():
    # The six-word title appears in both pieces, but far from where they meet.
    a = f"{_filler('a')} {_NAME} signed the order. {_filler('b')} Lamps out."
    b = f"Morning came grey. {_filler('c')} {_NAME} was waiting. {_filler('d')}"
    assert len(seams.words(_NAME)) == seams.N
    assert seams.max_overlap([a, b]) == 0


def test_a_repeat_across_the_seam_is_still_caught():
    # Piece b opens by re-telling the end of piece a.
    a = f"{_filler('a')} {_NAME} signed the order and the lamps went out."
    b = f"{_NAME} signed the order and the lamps went out. {_filler('b')}"
    assert seams.max_overlap([a, b]) >= 1
