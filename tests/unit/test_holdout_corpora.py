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
    paths = [  # evals.yml names them only behind holdout=yes (tested below)
        *(
            p
            for p in (ROOT / ".github" / "workflows").glob("*.yml")
            if p.name != "evals.yml"
        ),
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


def _evals_step(tmp_path, book: str, holdout: str, sentinel: str = "") -> tuple:
    """Run evals.yml's book-selection step with `python` stubbed to echo, so
    the shell logic is tested as written. Returns (exit code, evals.run calls)."""
    import subprocess

    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())
    step = next(
        s
        for s in wf["jobs"]["eval"]["steps"]
        if s.get("name", "").startswith("Run evals")
    )
    script = re.sub(r"\$\{\{[^}]*\}\}", "", step["run"])  # no other inputs
    (tmp_path / "evals").mkdir(exist_ok=True)
    (tmp_path / "evals" / ".run-eval").write_text(sentinel)
    env = {
        "PATH": "/usr/bin:/bin",
        "BOOK_INPUT": book,
        "HOLDOUT": holdout,
        "STEPS": "all",
        "BASE_LABEL": "ci",
    }
    stub = 'python() { [ "$2" = evals.run ] && echo "RUN $*"; return 0; }\n'
    out = subprocess.run(
        ["bash", "-c", stub + script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    runs = [ln for ln in out.stdout.splitlines() if ln.startswith("RUN ")]
    return out.returncode, runs, out.stdout


@pytest.mark.parametrize(
    "book, holdout, sentinel, books, flag",
    [
        ("", "no", "", ["dracula", "frankenstein", "cel"], False),  # push default
        ("", "no", "books: dracula", ["dracula"], False),  # the sentinel
        (
            "dracula frankenstein cel",
            "no",
            "",
            ["dracula", "frankenstein", "cel"],
            False,
        ),
        (
            "dracula frankenstein cel",
            "yes",
            "",
            HOLDOUT,
            True,
        ),  # the default means both
        ("holdout-b", "yes", "", ["holdout-b"], True),
    ],
)
def test_the_evals_workflow_runs_a_holdout_book_only_with_holdout_yes(
    tmp_path, book, holdout, sentinel, books, flag
):
    code, runs, _ = _evals_step(tmp_path, book, holdout, sentinel)
    assert code == 0
    assert [re.search(r"--book (\S+)", r).group(1) for r in runs] == books
    assert all(("--holdout" in r) == flag for r in runs)


@pytest.mark.parametrize(
    "book, holdout",
    [
        ("holdout-a", "no"),
        ("cel holdout-b", "no"),
        ("dracula", "yes"),
        ("holdout-a cel", "yes"),
    ],
)
def test_the_evals_workflow_refuses_a_mixed_or_unflagged_holdout_run(
    tmp_path, book, holdout
):
    code, runs, out = _evals_step(tmp_path, book, holdout)
    assert code == 1 and not runs and "::error title=Holdout run::" in out


# --- the renaming --------------------------------------------------------------


def _boundary(word: str) -> str:
    # '_' is a word character to `\b`, so an italicised `_Name_` hides from it.
    return rf"(?<![^\W_]){re.escape(word)}(?![^\W_])"


RENAMED = ["dracula", "frankenstein", "wuthering", *HOLDOUT]


def _originals(book: str) -> list[str]:
    spec = _json(book, "aliases.json")
    maps = [spec["aliases"], *spec.get("section_aliases", {}).values()]
    return sorted({k for m in maps for k in m})


def _corpus_texts(book: str) -> dict[str, str]:
    """Everything of a corpus that can reach a model: the excerpt, and
    spec.json / ground_truth.json (synopsis, cast, beats, lines) less the
    provenance fields and maintainer comments, which never leave the harness."""
    credit = {"_comment", "book", "author", "year", "source"}
    dump = lambda d: json.dumps(  # noqa: E731
        {k: v for k, v in d.items() if k not in credit}, ensure_ascii=False
    )
    texts = {"excerpts": _read(book, "excerpts.txt")}
    for name in ("spec.json", "ground_truth.json"):
        if (CORPORA / book / name).exists():
            texts[name] = dump(_json(book, name))
    synopsis = CORPORA / book / "synopsis.txt"
    if synopsis.exists():
        texts["synopsis.txt"] = synopsis.read_text(encoding="utf-8")
    return texts


def _leaks(originals: list[str], text: str) -> list[str]:
    """Whole names case-insensitively (all-caps and `_italic_` included), and
    any capitalised variant of a one-word original (a plural or adjective:
    'Lintons', 'Transylvanian')."""
    found = [src for src in originals if re.search(_boundary(src), text, re.I)]
    for src in (o for o in originals if " " not in o):
        found += re.findall(rf"(?<![A-Za-z]){re.escape(src)}[a-z]+", text)
    return found


@pytest.mark.parametrize("book", RENAMED)
def test_a_renamed_corpus_leaks_no_original_name(book):
    """(F) SPEC-94: all five renamed corpora, development and holdout."""
    for where, text in _corpus_texts(book).items():
        assert not _leaks(_originals(book), text), where


def test_the_synthetic_corpus_carries_no_renamed_original():
    """cel has no source to rename; none of the other corpora's originals
    appears in it either."""
    originals = sorted({o for b in RENAMED for o in _originals(b)})
    for where, text in _corpus_texts("cel").items():
        assert not _leaks(originals, text), where


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
