"""SPEC-94 S5: the frozen holdout corpora and the development/holdout guard.

These checks are mechanical (D19-4): names renamed, no original left, structure
and counts. They read the holdout files directly, never through the corpus
readers a run uses, and grade nothing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from evals import splits
from evals.steps import pipeline
from evals.tools import build_gt, rename

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "evals" / "corpora"
DEVELOPMENT = ["cel", "dracula", "frankenstein", "wuthering"]
HOLDOUT = ["holdout-a", "holdout-b"]


def _read(book: str, name: str) -> str:
    return (CORPORA / book / name).read_text(encoding="utf-8")


def _json(book: str, name: str) -> dict:
    return json.loads(_read(book, name))


# --- the split ---------------------------------------------------------------


def test_every_corpus_declares_its_split():
    dirs = sorted(d.name for d in CORPORA.iterdir() if d.is_dir())
    assert dirs == sorted(DEVELOPMENT + HOLDOUT)
    assert splits.corpora("development") == DEVELOPMENT
    assert splits.corpora("holdout") == HOLDOUT


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_run_reads_a_holdout_corpus_only_when_it_names_it(book, monkeypatch):
    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    with pytest.raises(SystemExit, match="holdout corpus"):
        pipeline.load_corpus(book)
    with pytest.raises(SystemExit, match="holdout corpus"):
        pipeline.load_excerpts(book)
    other = next(b for b in HOLDOUT if b != book)
    monkeypatch.setenv(splits.ALLOW_ENV, other)  # naming the other one is not enough
    with pytest.raises(SystemExit):
        pipeline.load_excerpts(book)
    monkeypatch.setenv(splits.ALLOW_ENV, book)
    text, gt = pipeline.load_corpus(book)
    assert gt["book"] == book and text  # asked for by name: readable


@pytest.mark.parametrize("book", DEVELOPMENT)
def test_development_corpora_need_no_request(book, monkeypatch):
    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    assert splits.allowed(book) and pipeline.load_excerpts(book)


def test_the_runner_refuses_a_holdout_book_without_holdout(monkeypatch):
    from evals import run

    monkeypatch.delenv(splits.ALLOW_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["evals.run", "--book", "holdout-a"])
    with pytest.raises(SystemExit, match="holdout corpus"):
        run.main()
    monkeypatch.setattr(sys, "argv", ["evals.run", "--book", "dracula", "--holdout"])
    with pytest.raises(SystemExit, match="not a holdout corpus"):
        run.main()


def test_no_default_or_workflow_selects_a_holdout_corpus():
    """No default book, CI workflow, run sentinel or evals module names one: a
    holdout run is always an explicit `--holdout` (D19-4)."""
    paths = [
        *(ROOT / ".github" / "workflows").glob("*.yml"),
        *(ROOT / "evals").rglob("*.py"),
        *(ROOT / "scripts").glob("*"),
        ROOT / "evals" / "history.csv",
        ROOT / "evals" / ".run-eval",
    ]
    for p in paths:
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            for book in HOLDOUT:
                assert not re.search(rf"\b{book}\b", text), f"{p} names {book}"
    for module in ("run.py", "offline.py"):
        src = (ROOT / "evals" / module).read_text(encoding="utf-8")
        default = re.search(r'"--book", default="([\w-]+)"', src).group(1)
        assert splits.split(default) == "development", module


# --- the renaming --------------------------------------------------------------


def _boundary(word: str) -> str:
    # '_' is a word character to `\b`, so an italicised `_Name_` hides from it.
    return rf"(?<![^\W_]){re.escape(word)}(?![^\W_])"


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_holdout_corpus_leaks_no_original_name(book):
    """No original survives: whole names case-insensitively (all-caps and
    `_italic_` included), and no capitalised variant of a one-word original
    (a plural, possessive or adjective: 'Lintons', 'Transylvanian')."""
    aliases = _json(book, "aliases.json")["aliases"]
    spec = _json(book, "spec.json")
    gt = _json(book, "ground_truth.json")
    texts = {
        "excerpts": _read(book, "excerpts.txt"),
        "spec": json.dumps({k: v for k, v in spec.items() if k != "source"}),
        "ground_truth": json.dumps({k: v for k, v in gt.items() if k != "source"}),
    }
    for where, text in texts.items():
        for src in aliases:
            assert not re.search(_boundary(src), text, re.I), (where, src)
            if " " not in src:
                variant = re.search(rf"(?<![A-Za-z]){re.escape(src)}[a-z]", text)
                assert not variant, (where, src, variant and variant.group(0))


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_holdout_rename_is_one_to_one(book):
    aliases = _json(book, "aliases.json")["aliases"]
    originals = {w for k in aliases for w in k.split()}
    for src, rep in aliases.items():
        same = [set(k.split()) for k, v in aliases.items() if v == rep]
        assert all(k <= set(src.split()) or set(src.split()) <= k for k in same)
        assert not set(rep.split()) & originals, rep


# --- the structure -------------------------------------------------------------


@pytest.mark.parametrize("book", HOLDOUT)
def test_a_holdout_corpus_has_the_development_shape(book):
    spec = _json(book, "spec.json")
    gt = _json(book, "ground_truth.json")
    aliases = _json(book, "aliases.json")
    text = _read(book, "excerpts.txt")
    assert spec["split"] == "holdout"
    # The excerpt's headings are exactly the declared sections, in order.
    heads = re.findall(r"(?m)^CHAPTER [IVXLC]+$", text)
    assert heads == aliases["sections"] == [s["heading"] for s in aliases["spans"]]
    # A few chapters, sized like the development corpora (56k-160k chars).
    assert 4 <= len(heads) <= 7 and 100_000 <= len(text) <= 160_000
    # Every narrator's chapters exist, and each has a full train/test split.
    for who, src in spec["voice_sources"].items():
        assert set(src["chapters"]) <= set(heads), who
        lines = gt["gold_lines"][who]
        assert len(lines["train"]) == 8 and len(lines["test"]) == 3, who
    # Every cast member is named in the excerpt (the surname at least).
    for name in spec["cast"]:
        assert name.split()[-1] in text, name
    # Each injection's anchor sits once inside the graded continuity window.
    window = text[:12000]
    for inj in spec["continuity_injections"]:
        assert window.count(inj["locate"]) == 1, inj["id"]
    assert [b["kind"] for b in spec["canonical_beats"]][0] == "inciting"
    # ground_truth.json is exactly what build_gt makes from these files.
    assert build_gt.build(book) == gt


# --- the build tool ------------------------------------------------------------


def test_apply_aliases_renames_italics_and_keeps_case():
    out = rename.apply_aliases(
        "_Earnshaw_ met EARNSHAW and Earnshaw’s dog.", {"Earnshaw": "Ashdown"}
    )
    assert out == "_Ashdown_ met ASHDOWN and Ashdown’s dog."


def test_extract_spans_heads_each_span_and_drops_the_source_heading():
    body = "I\n\nOne starts here. Walter.\n\nII\n\nTwo starts. Walter.\n\nIII\n\nx"
    spans = [
        {"heading": "CHAPTER I", "start": "Two starts.", "end": "\n\nIII\n\n"},
        {"heading": "CHAPTER II", "start": "One starts", "end": "\n\nII\n\n"},
    ]
    out = rename.extract_spans(body, spans, {"Walter": "Edwin"}, {})
    assert (
        out
        == "CHAPTER I\n\nTwo starts. Edwin.\n\n\nCHAPTER II\n\nOne starts here. Edwin."
    )
    with pytest.raises(SystemExit, match="not unique"):
        rename.extract_spans(body, [{**spans[0], "start": "Walter"}], {}, {})
    with pytest.raises(SystemExit, match="distinct HEADING"):
        rename.extract_spans(body, [spans[0], spans[0]], {}, {})
