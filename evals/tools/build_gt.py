"""Assemble ground_truth.json for a book from its renamed excerpts.

Gold voice lines are extracted programmatically (real renamed public-domain
prose, not paraphrase) per character from their narration sections, then split
train/test. Cast / canonical_beats / continuity_injections are authored in
SPEC below (they encode knowledge of the source's structure). Run once, offline,
after rename.py; commit the resulting ground_truth.json.

    python -m evals.tools.build_gt --book dracula
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CORPORA = Path(__file__).resolve().parent.parent / "corpora"

# Per-book authored spec. Locators pick each character's narration by a text
# span (start marker -> end marker) within the renamed excerpts; paragraphs in
# that span become that character's gold voice. Markers are renamed strings.
SPEC = {
    "dracula": {
        "author": "Bram Stoker",
        "year": 1897,
        "source": "Standard Ebooks / Project Gutenberg (public domain, US)",
        # Extraction ground truth: the major named cast present in the excerpts.
        "cast": [
            "Aldous Kerr",
            "Count Vasska",
            "Nora Vance",
            "Cora Ellis",
            "Elias Ward",
            "Verhoeven",
            "Cassidy Boone",
        ],
        # Voice gold sources per character. Two kinds:
        #   {"chapter": "CHAPTER II"}  -> the whole chapter body is this narrator's voice
        #   {"letters": r"regex"}      -> each letter block matching the regex is theirs
        # Three distinct voices from DISJOINT single-narrator chapters — disjoint
        # spans guarantee no line is attributed to two characters. II is the
        # traveller's castle journal, VIII the young woman's Whitby journal, X
        # the physician's clinical asylum diary.
        "voice_sources": {
            "Aldous Kerr": {"chapters": ["CHAPTER II"]},
            "Nora Vance": {"chapters": ["CHAPTER VIII"]},
            "Elias Ward": {"chapters": ["CHAPTER X"]},
        },
        # Book-level plot ground truth (renamed) for the outline-reconstruction eval.
        "synopsis": (
            "A young solicitor, Aldous Kerr, travels to a remote castle to close a "
            "property sale for the mysterious Count Vasska, and slowly realizes he is "
            "a prisoner and his host is no living man. Back home, his fiancee Nora and "
            "her friend Cora are drawn into a spreading horror as Vasska arrives by sea. "
            "The physician Elias Ward and the learned Professor Verhoeven recognize the "
            "mark of the undead, and a small band must destroy Vasska before he claims "
            "them all."
        ),
        "canonical_beats": [
            {"title": "The prisoner in the castle", "kind": "inciting"},
            {"title": "Vasska arrives by sea; a friend sickens", "kind": "rising"},
            {"title": "Verhoeven names the undead", "kind": "midpoint"},
            {"title": "The band hunts Vasska's resting places", "kind": "rising"},
            {"title": "Nora is attacked and marked", "kind": "crisis"},
            {
                "title": "Pursuit back to the castle and Vasska's destruction",
                "kind": "climax",
            },
        ],
        # Continuity ground truth: planted contradictions for the detection eval.
        # Each injects `replace` at `locate` in a working copy; the checker should
        # flag a finding of `expect_type`.
        "continuity_injections": [
            {
                "id": "name-misspell",
                "locate": "Aldous Kerr",
                "replace": "Aldous Karr",
                "expect_type": "character",
                "note": "surname respelled mid-text",
            },
            {
                "id": "date-flip",
                "locate": "_5 May._",
                "replace": "_5 August._",
                "expect_type": "timeline",
                "note": "journal date jumps backwards out of sequence",
            },
        ],
    }
}


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
    spec = SPEC[book]
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
