"""SPEC-94: no corpus id or original title reaches the canon block.

An eval book's title is the first line of the canon block every planning prompt
carries (`Title: …`). It used to be `eval-<corpus>` (`eval-dracula`), which names
the novel to the model under test. Titles, sources and filenames now carry an
opaque per-corpus tag instead (`pipeline.eval_title` / `corpus_tag`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.offline import corpus_canon
from evals.steps import pipeline

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ROOT / "evals" / "corpora"
BOOKS = sorted(d.name for d in CORPORA.iterdir() if (d / "spec.json").exists())
PURPOSES = ["", "voice", "ing", "attr", "prose", "revise", "ens", "baseline"]


def _original_titles() -> list[str]:
    """Each source novel's title from its PROVENANCE.md ('Frankenstein; or, The
    Modern Prometheus' gives 'Frankenstein'), less a leading article."""
    out = []
    for p in CORPORA.glob("*/PROVENANCE.md"):
        m = re.search(r"\*\*Title:\*\* \*([^*]+)\*", p.read_text(encoding="utf-8"))
        if m:
            out.append(re.sub(r"^The ", "", m.group(1).split(";")[0]).strip())
    return out


TITLES = _original_titles()
# The synthetic cel names no novel, and "the CEL" is its own in-world term, so
# its id is checked in titles only; every other id names (or stands for) a novel.
CUES = [*(b for b in BOOKS if b != "cel"), *TITLES]


def _cued(text: str, cues: list[str] = CUES) -> list[str]:
    return [c for c in cues if re.search(rf"\b{re.escape(c)}\b", text, re.I)]


def test_the_original_titles_are_known():
    assert {"Dracula", "Wuthering Heights", "Woman in White", "Moonstone"} <= set(
        TITLES
    )


@pytest.mark.parametrize("book", BOOKS)
def test_an_eval_title_is_opaque_stable_and_per_corpus(book):
    tag = pipeline.corpus_tag(book)
    assert re.fullmatch(r"[0-9a-f]{8}", tag) and tag == pipeline.corpus_tag(book)
    for purpose in PURPOSES:
        title = pipeline.eval_title(book, purpose)
        assert title.endswith(tag) and title.startswith("eval-")
        assert not _cued(title, [*BOOKS, *TITLES]), title
    others = {pipeline.corpus_tag(b) for b in BOOKS if b != book}
    assert tag not in others


@pytest.mark.parametrize("book", BOOKS)
def test_the_canon_block_names_no_corpus_or_original_title(book):
    gt = json.loads((CORPORA / book / "ground_truth.json").read_text("utf-8"))
    block = corpus_canon(gt).full_block()
    title = block.splitlines()[0]
    assert title == f"Title: {pipeline.eval_title(book)}"
    assert not _cued(title, [*BOOKS, *TITLES]) and not _cued(block), book


def test_no_eval_title_or_upload_name_is_built_from_the_corpus_id():
    """Every title, source title and upload filename goes through
    `eval_title` / `corpus_tag`, never the corpus id itself."""
    built = re.compile(
        r"""f["'](?:eval-[^"']*)?\{(?:ctx\.book|gt\[["']book["']\]|book|args\.book)\}"""
        r"""(?:[^"']*\.txt)?["']"""
    )
    # evals/baseline stays frozen at W12-9's P⁵ (the gate's p-freeze row) until
    # that result is published; its arms share one title, so the cue is common
    # to both and does not bias the comparison (SPEC-94 D19-10).
    frozen = ROOT / "evals" / "baseline"
    for p in [*(ROOT / "evals").rglob("*.py"), *(ROOT / "scripts").glob("*")]:
        if p.is_file() and frozen not in p.parents:
            src = p.read_text(encoding="utf-8", errors="replace")
            assert not built.search(src), f"{p}: {built.search(src).group(0)}"


def test_the_prose_cache_key_moved_with_the_titles():
    """Nit: the opaque titles (D19-10) change what generation saw (the title
    reaches the canon prompt), so a prose generation cached under the old
    titles is regenerated, never regraded as if it were this harness's."""
    from evals.steps import pipeline

    assert pipeline.PROSE_CACHE_KEY == "planned-v11"  # v10: corpus-named titles
