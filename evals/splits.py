"""Development and holdout corpora (SPEC-94 S5, D19-3 / D19-4).

Every corpus declares `"split"` in its `spec.json`. `development` corpora
(frankenstein, dracula, wuthering, cel) are what the harness and the product
have been tuned against. `holdout` corpora are built once, frozen at a tag
(`holdout-v1`) and run once per release on that release's frozen code, so a
gain on them is evidence the result holds on books the system was not shaped
around. A run that reads one by default, or that someone looks at and then
"fixes for", stops it being a holdout.

So the corpus readers (`evals.steps.pipeline.load_corpus` / `load_excerpts`)
refuse a holdout corpus unless the run asks for it by name:
`python -m evals.run --book <holdout> --holdout`, which sets `EVAL_HOLDOUT`
to that book. Nothing else sets it, and no default book list names a holdout
corpus (`tests/unit/test_holdout_corpora.py`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CORPORA = Path(__file__).resolve().parent / "corpora"

# The explicit request: a comma list of the holdout books this process may read.
ALLOW_ENV = "EVAL_HOLDOUT"

SPLITS = ("development", "holdout")


def split(book: str) -> str:
    """The corpus' declared split; a name with no spec.json (no such corpus,
    or a test's fixture) reads as development, so its own error surfaces."""
    spec = CORPORA / book / "spec.json"
    if not spec.exists():
        return "development"
    got = json.loads(spec.read_text(encoding="utf-8")).get("split")
    if got not in SPLITS:
        raise ValueError(f"{book}: spec.json split must be one of {SPLITS}: {got!r}")
    return got


def corpora(which: str) -> list[str]:
    """The corpus directories of one split, sorted."""
    return sorted(
        d.name
        for d in CORPORA.iterdir()
        if (d / "spec.json").exists() and split(d.name) == which
    )


def allowed(book: str) -> bool:
    """A development corpus always; a holdout corpus only when `EVAL_HOLDOUT`
    names it."""
    if split(book) != "holdout":
        return True
    named = {b.strip() for b in os.environ.get(ALLOW_ENV, "").split(",")}
    return book in named


def require(book: str) -> None:
    """Refuse to read a holdout corpus that was not explicitly requested."""
    if not allowed(book):
        raise SystemExit(
            f"{book} is a holdout corpus (SPEC-94 D19-4): it runs once per release "
            f"on frozen code, never in development. Ask for it explicitly with "
            f"`python -m evals.run --book {book} --holdout` ({ALLOW_ENV}={book})."
        )
