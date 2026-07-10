"""Assemble ground_truth.json for a book from its renamed excerpts + spec.json.

Gold voice lines are extracted programmatically (real renamed public-domain
prose, not paraphrase) per character from their narration sections, then split
train/test. Everything book-specific — cast, voice sources, synopsis, canonical
beats, continuity injections — is DATA in `corpora/<book>/spec.json`, so adding
a book needs no code change here. Run once, offline, after rename.py.

    python -m evals.tools.build_gt --book dracula
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CORPORA = Path(__file__).resolve().parent.parent / "corpora"


def load_spec(book: str) -> dict:
    return json.loads((CORPORA / book / "spec.json").read_text(encoding="utf-8"))


def _paras(span: str, minlen=140, maxlen=1200):
    """Narration paragraphs — long enough to carry voice, not dialogue fragments
    or headers. The source uses long paragraphs, hence the wide window."""
    out = []
    for p in span.split("\n\n"):
        p = re.sub(r"\s+", " ", p).strip()
        if minlen <= len(p) <= maxlen and not p.startswith(("_", "“", "CHAPTER")):
            out.append(p)
    return out


# Any letter/telegram/chapter/diary/journal header ends a letter block, so a
# letter's paragraphs never bleed into the diary text that follows it.
_LETTER_HEADER = re.compile(
    r"(?m)^(?:_?(?:Letter|Telegram)[^\n]*|CHAPTER [IVXLC]+\b[^\n]*|"
    r"[A-Z][A-Z .’'—-]{4,40}(?:JOURNAL|DIARY)[^\n]*)$"
)


def _chapter_body(text: str, chapter: str) -> str:
    i = text.find(chapter + "\n")
    if i == -1:
        raise SystemExit(f"chapter not found: {chapter!r}")
    nxt = re.search(r"(?m)^CHAPTER [IVXLC]+\s*$", text[i + len(chapter) :])
    return text[i : i + len(chapter) + (nxt.start() if nxt else len(text))]


def _block_lines(text: str, pattern: str) -> list[str]:
    """Paragraphs of every block whose header matches `pattern` (letter or diary):
    from the header to the next header of any kind."""
    lines: list[str] = []
    heads = [m.start() for m in _LETTER_HEADER.finditer(text)]
    for m in re.finditer(pattern, text, flags=re.IGNORECASE):
        start = m.start()
        end = next((h for h in heads if h > start + 1), len(text))
        lines.extend(_paras(text[start:end]))
    return lines


def build(book: str) -> dict:
    spec = load_spec(book)
    text = (CORPORA / book / "excerpts.txt").read_text(encoding="utf-8")
    gold = {}
    for char, src in spec["voice_sources"].items():
        if "chapters" in src:
            lines = []
            for ch in src["chapters"]:
                lines += _paras(_chapter_body(text, ch))
        else:
            lines = _block_lines(text, src["blocks"])
        # de-dup, keep order
        seen, uniq = set(), []
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                uniq.append(ln)
        lines = uniq
        if len(lines) < 4:
            raise SystemExit(f"too few gold lines for {char!r}: {len(lines)}")
        cut = max(2, int(len(lines) * 0.8))
        gold[char] = {"train": lines[:cut], "test": lines[cut:]}
    return {
        "book": book,
        "author": spec["author"],
        "year": spec["year"],
        "source": spec["source"],
        "cast": spec["cast"],
        "synopsis": spec["synopsis"],
        "gold_lines": gold,
        "canonical_beats": spec["canonical_beats"],
        "continuity_injections": spec["continuity_injections"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    args = ap.parse_args()
    gt = build(args.book)
    out = CORPORA / args.book / "ground_truth.json"
    out.write_text(
        json.dumps(gt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    counts = {
        c: f"{len(v['train'])}tr/{len(v['test'])}te"
        for c, v in gt["gold_lines"].items()
    }
    print(f"wrote {out}: cast={len(gt['cast'])}, gold={counts}")


if __name__ == "__main__":
    main()
