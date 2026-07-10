"""Unit tests for the manuscript sampler used in character extraction.

Pure logic, no LLM: the sampler decides WHICH text the extractor sees. The old
head-only `text[:10000]` made late-introduced characters invisible; these tests
pin that the whole span is now covered within a fixed budget.
"""

import pytest

from app.parsing.character_extractor import stratified_sample

pytestmark = pytest.mark.unit


def test_short_text_returned_whole():
    text = "a paragraph.\n\nanother."
    assert stratified_sample(text, budget=14000) == text


def test_covers_head_and_tail_within_budget():
    # A late marker past the old 10k head must survive sampling.
    head = "HEAD_MARKER " + ("x " * 8000)  # ~16k chars of filler after the head
    text = head + "TAIL_MARKER"
    out = stratified_sample(text, budget=14000, windows=4)
    assert "HEAD_MARKER" in out  # first window anchored at the start
    assert "TAIL_MARKER" in out  # final window reaches the tail
    assert len(out) <= 14000 + 4 * len("\n\n[...]\n\n")


def test_samples_from_the_middle_too():
    # Ten labelled regions across the doc; a window landing in a region picks up
    # its tag. The output must span >2 regions — i.e. genuinely spread, not just
    # head+tail. Region-fill (vs point markers) is robust to exact window starts.
    regions = 10
    seg = 4000
    text = "".join(f"REGION{r:02d} " * (seg // 9) for r in range(regions))
    out = stratified_sample(text, budget=14000, windows=4)
    hit = {r for r in range(regions) if f"REGION{r:02d}" in out}
    assert len(hit) >= 3  # more than just the two endpoints
    assert 0 in hit  # head covered
    assert (regions - 1) in hit  # tail covered


def test_windows_do_not_overlap_backwards():
    # Monotonic, non-overlapping windows: output length stays bounded even if
    # the computed starts bunch up on a short-ish text.
    text = "y" * 20000
    out = stratified_sample(text, budget=14000, windows=4)
    assert len(out.replace("\n\n[...]\n\n", "")) <= 14000
