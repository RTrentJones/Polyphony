"""Shared section-heading grammar for the corpus tools.

A corpus is sliced into sections at top-level headings. Different public-domain
editions head their sections differently — Dracula uses roman-numeral chapters
(`CHAPTER II`), Frankenstein uses arabic chapters and letters (`Chapter 5`,
`Letter 4`). This is the single superset pattern both `rename.py` and
`build_gt.py` match, so a new book with a supported heading style needs no code
change. It is a strict superset of the original `CHAPTER [IVXLC]+` form, so
existing corpora slice byte-identically.
"""

from __future__ import annotations

# One heading token — extend here (only here) to support a new edition's style.
HEADING = r"(?:CHAPTER [IVXLC]+|Chapter \d+|Letter \d+)"

# A heading anchored at the start of a line, capturing the rest of the line.
HEADING_LINE = rf"(?m)^{HEADING}\b.*$"

# A heading that is alone on its line (used to find the NEXT section boundary).
HEADING_ALONE = rf"(?m)^{HEADING}\s*$"
