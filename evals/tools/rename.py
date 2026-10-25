"""Produce a renamed, public-domain excerpt corpus from a source ebook.

Why renaming: a finished novel is ground truth for the pipeline (known cast,
real per-character voice, known plot). But feeding a model the *original* text
tests its memory of the book, not the pipeline. Renaming every character/place
keeps the (public-domain) prose and the ground truth intact while removing the
name cue. It reduces name-cued recall; it does not defeat memorization of a
famous novel, whose prose is recognisable without the names (SPEC-94 S3).

Legal: source texts are US public domain; we source CC0 editions (Standard
Ebooks) or strip the Project Gutenberg wrapper (their license: a text with all
PG references removed "is left with a text unrestricted by U.S. intellectual
property law"). Only the RENAMED DERIVATIVE excerpts are committed — never the
raw source, never whole novels. See evals/corpora/<book>/PROVENANCE.md.

This tool is run ONCE, offline, to regenerate a book's `excerpts.txt` from its
`aliases.json`; it is not part of the eval run.

Usage:
    python -m evals.tools.rename --book dracula --source /path/to/source.txt
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from evals.tools.headings import HEADING, HEADING_LINE

CORPORA = Path(__file__).resolve().parent.parent / "corpora"


def strip_gutenberg(text: str) -> str:
    """Remove the Project Gutenberg header/footer, leaving the public-domain body."""
    start = re.search(r"\*\*\* START OF THE PROJECT GUTENBERG[^\n]*\*\*\*", text)
    end = re.search(r"\*\*\* END OF THE PROJECT GUTENBERG[^\n]*\*\*\*", text)
    body = text[(start.end() if start else 0) : (end.start() if end else len(text))]
    # Belt-and-braces: drop any stray "Project Gutenberg" trademark references.
    body = re.sub(r"[^\n]*Project Gutenberg[^\n]*\n?", "", body)
    return body.strip()


def _match_case(sample: str, replacement: str) -> str:
    """Cast `replacement` to the case pattern of `sample` (ALL-CAPS or Title)."""
    if sample.isupper():
        return replacement.upper()
    if sample[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_aliases(text: str, aliases: dict[str, str]) -> str:
    """Whole-word replace every original name/place with its rename.

    Longest keys first so multi-word names (e.g. 'Van Helsing') are replaced
    before their parts. Word-boundary anchored, case-INSENSITIVE match with
    case-PRESERVING output — so 'JONATHAN' in an all-caps header and 'Jonathan'
    in prose both convert (to 'ALDOUS' / 'Aldous'). The boundary treats '_' as
    a non-letter, so an italicised '_Jonathan_' converts too (a plain regex
    `\b` does not, since '_' is a word character: Wuthering's '_Earnshaw_').
    Plurals and adjectival forms ('Lintons', 'Transylvanian') are separate
    words and need their own keys.
    """
    for src in sorted(aliases, key=len, reverse=True):
        rep = aliases[src]
        text = re.sub(
            rf"(?<![^\W_]){re.escape(src)}(?![^\W_])",
            lambda m: _match_case(m.group(0), rep),
            text,
            flags=re.IGNORECASE,
        )
    return text


def extract_sections(body: str, sections: list[str], aliases: dict, per: dict) -> str:
    """Slice the named top-level sections (e.g. 'CHAPTER II'), rename each (its own
    per-section overrides win), and concatenate.

    A section runs from its header line to the next chapter/section header. This
    keeps the committed corpus to a curated few chapters, not the whole book.
    """
    # Any section heading (chapter or letter) marks a boundary.
    boundaries = [
        (m.start(), m.group(0).strip()) for m in re.finditer(HEADING_LINE, body)
    ]
    out = []
    for i, (pos, name) in enumerate(boundaries):
        if name.split(".")[0].strip() not in sections and name not in sections:
            continue
        nxt = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(body)
        chunk = body[pos:nxt].strip()
        # Skip table-of-contents entries (short) — only real chapter bodies.
        if len(chunk) < 1000:
            continue
        out.append(apply_aliases(chunk, aliases | per.get(name, {})))
    if not out:
        raise SystemExit(f"none of {sections} found among {[b[1] for b in boundaries]}")
    return "\n\n\n".join(out)


def extract_spans(body: str, spans: list[dict], aliases: dict, per: dict) -> str:
    """Slice explicit spans, head each with its own excerpt heading, rename, and
    concatenate.

    For an edition whose own headings repeat or fall outside the shared grammar
    (Collins's narratives restart their numbering per narrator, and some are
    bare numerals): each span is ``{"heading": "CHAPTER III", "start": "<literal,
    unique in the body>", "end": "<literal>"}``. The excerpt holds ``heading``,
    then the source from ``start`` (inclusive) to the first ``end`` after it
    (exclusive), so the source's own heading is not copied. ``heading`` must
    match the shared grammar (`HEADING`), and every span's heading is distinct,
    so `build_gt` and the narrator samples find each chapter by name.
    """
    out, seen = [], set()
    for sp in spans:
        head, start, end = sp["heading"], sp["start"], sp["end"]
        if not re.fullmatch(HEADING, head) or head in seen:
            raise SystemExit(f"span heading not a distinct HEADING: {head!r}")
        seen.add(head)
        if body.count(start) != 1:
            raise SystemExit(f"span start not unique in the source: {start[:40]!r}")
        i = body.index(start)
        j = body.find(end, i + len(start))
        if j == -1:
            raise SystemExit(f"span end not found after its start: {end[:40]!r}")
        chunk = head + "\n\n" + body[i:j].strip()
        out.append(apply_aliases(chunk, aliases | per.get(head, {})))
    return "\n\n\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True, help="corpus dir under evals/corpora/")
    ap.add_argument("--source", required=True, help="raw source ebook (.txt)")
    args = ap.parse_args()

    book_dir = CORPORA / args.book
    spec = json.loads((book_dir / "aliases.json").read_text())
    raw = Path(args.source).read_text(encoding="utf-8")
    # Normalize line endings — Gutenberg .txt ships CRLF, and the section
    # slicing / gold-line extraction anchor on "\n". No-op on an LF source.
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    body = strip_gutenberg(raw)
    # A section may give a name another meaning (Wuthering's two Catherines).
    per = spec.get("section_aliases", {})
    if "spans" in spec:  # explicit spans (the holdout corpora)
        renamed = extract_spans(body, spec["spans"], spec["aliases"], per)
    else:
        renamed = extract_sections(body, spec["sections"], spec["aliases"], per)

    # Sanity: no original name should survive the rename (case-insensitive).
    leaks = [
        src
        for src in spec["aliases"] | {k: 0 for v in per.values() for k in v}
        if re.search(
            rf"(?<![^\W_]){re.escape(src)}(?![^\W_])", renamed, flags=re.IGNORECASE
        )
    ]
    if leaks:
        raise SystemExit(f"rename leak — originals still present: {leaks}")

    out = book_dir / "excerpts.txt"
    out.write_text(renamed + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(renamed):,} chars, sections={spec['sections']})")


if __name__ == "__main__":
    main()
