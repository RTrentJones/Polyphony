"""SPEC-96 M4: the outline lint, by script ($0, no LLM). Descriptive, no arm.

Reads the outline every `chapters` report carries (`steps.chapters.outline`,
the portable form: each node's cast as names, E4's outline-only runs and its
chapters runs alike) and, per distinct outline (by `outline_digest`; a fixed
outline stored into several runs is one outline, its runs listed), reports:

- `beats_no_character`: the share of planned beats (a chapter node's
  children) whose `characters` list is empty.
- `pov_missing`: chapters with no `pov`. The portable form keeps a POV only
  when it names the book's cast (`pipeline.portable_outline` drops any other,
  so a POV outside the cast and an unset POV both land here).
- `pov_outside_cast`: chapters whose `pov` is set but is not a cast member
  (`app.parsing.names.match` against the corpus cast); 0 in the portable
  form by construction, kept for an outline from elsewhere.
- `cast_not_resolved`: names in a node's `characters` that no cast member
  matches (again 0 in the portable form by construction).
- `secondary_names`: the names nodes carry outside the structural cast (the
  outline book's minor characters), and how many of them the book's source
  (its excerpts) or synopsis never names.
- `invented`: capitalised names in the outline's text (summaries, POV,
  threads, people; titles left out, as the product's `audit_outline` does)
  that neither the cast, the corpus' known names, the synopsis nor the
  book's source excerpts name: `pipeline._unknown_canon`, the product's
  invented-name warning, with the excerpts as context; `invented_rate` is
  their share of the distinct proper nouns.

Per book: the same, averaged over its distinct outlines.

python -m evals.narration.lint REPORT_OR_DIR... [--json OUT]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from evals.narration.contract import report_files, run_id


def _ground_truth(book: str) -> dict:
    from evals.steps.pipeline import CORPORA

    return json.loads((CORPORA / book / "ground_truth.json").read_text("utf-8"))


def _in_cast(name: str, cast: list[str]) -> bool:
    from app.parsing.names import match

    return any(match(name, c) for c in cast)


def lint(outline: list, book: str) -> dict:
    """M4 for one portable outline of `book` (module docstring)."""
    from app.planning.fidelity import flatten_outline
    from evals.steps.pipeline import _unknown_canon, load_excerpts, named_in

    gt = _ground_truth(book)
    cast = list(gt.get("cast") or [])
    source = load_excerpts(book)
    chapters = [c for c in outline or [] if isinstance(c, dict)]
    beats = [
        b for c in chapters for b in c.get("children") or [] if isinstance(b, dict)
    ]
    empty = [b.get("title") for b in beats if not b.get("characters")]
    no_pov = [c.get("title") for c in chapters if not c.get("pov")]
    pov_out = [
        c["pov"] for c in chapters if c.get("pov") and not _in_cast(c["pov"], cast)
    ]
    named = {n for node in chapters + beats for n in node.get("characters") or []}
    unresolved = sorted(n for n in named if not _in_cast(n, cast))
    secondary = sorted(
        {n for node in chapters + beats for n in node.get("secondary_names") or []}
    )
    hay = f"{gt.get('synopsis') or ''}\n{source}"
    unsourced = [n for n in secondary if not named_in(hay, [n])]
    _, body = flatten_outline(chapters)
    rate, invented = _unknown_canon("\n".join(body), gt, context=source)
    return {
        "chapters": len(chapters),
        "beats": len(beats),
        "beats_no_character": round(len(empty) / len(beats), 4) if beats else None,
        "beats_no_character_titles": empty,
        "pov_missing": len(no_pov),
        "pov_missing_titles": no_pov,
        "pov_outside_cast": pov_out,
        "cast_not_resolved": unresolved,
        "secondary_names": secondary,
        "secondary_unsourced": unsourced,
        "invented": sorted(invented),
        "invented_rate": rate,
    }


def collect(paths: list[Path]) -> list[dict]:
    """One row per distinct (book, outline digest), with the runs that carry it."""
    from evals.chapters.generate import digest

    found: dict = {}
    for f in report_files(paths):
        r = json.loads(f.read_text("utf-8"))
        res = (r.get("steps") or {}).get("chapters") or {}
        outline = res.get("outline")
        if not isinstance(outline, list) or not outline:
            continue
        d = res.get("outline_digest") or digest(outline)
        row = found.setdefault(
            (r.get("book"), d),
            {
                "book": r.get("book"),
                "outline_digest": d,
                "outline": outline,
                "runs": [],
            },
        )
        row["runs"].append({"run": run_id(f, r), "source": res.get("outline_source")})
    return [found[k] for k in sorted(found)]


def analyze(paths: list[Path]) -> dict:
    rows = []
    for row in collect(paths):
        rows.append(
            {k: v for k, v in row.items() if k != "outline"}
            | lint(row["outline"], row["book"])
        )
    books: dict = defaultdict(list)
    for r in rows:
        books[r["book"]].append(r)
    per_book = {}
    for book, rs in sorted(books.items()):
        share = [
            r["beats_no_character"] for r in rs if r["beats_no_character"] is not None
        ]
        per_book[book] = {
            "outlines": len(rs),
            "beats_no_character": round(mean(share), 4) if share else None,
            "pov_missing_per_outline": round(mean(r["pov_missing"] for r in rs), 4),
            "chapters_per_outline": round(mean(r["chapters"] for r in rs), 4),
            "pov_outside_cast": sum(len(r["pov_outside_cast"]) for r in rs),
            "cast_not_resolved": sum(len(r["cast_not_resolved"]) for r in rs),
            "secondary_unsourced": sum(len(r["secondary_unsourced"]) for r in rs),
            "invented_rate": round(mean(r["invented_rate"] for r in rs), 4),
        }
    return {
        "label": "M4 outline lint, SPEC-96; descriptive",
        "outlines": rows,
        "books": per_book,
    }


def render(res: dict) -> str:
    lines = [
        "M4 outline lint (descriptive; per distinct outline)",
        f"  {'book':13s} {'digest':9s} {'runs':>4s} beats no-char pov-missing "
        "invented",
    ]
    for r in res["outlines"]:
        lines.append(
            f"  {r['book']:13s} {r['outline_digest'][:8]:9s} {len(r['runs']):4d} "
            f"{r['beats']:5d} {r['beats_no_character']!s:>7s} "
            f"{r['pov_missing']:>5d}/{r['chapters']:<5d} "
            f"{r['invented_rate']} {r['invented']}"
        )
    for book, b in res["books"].items():
        lines.append(f"  {book}: {json.dumps(b)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("reports", type=Path, nargs="+")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    res = analyze(a.reports)
    print(render(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
