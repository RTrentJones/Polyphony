# SPEC-64 — The evidence stand-in says what is true

**Closes**: W8-6 (S-2) · **Size**: S · **Amends**: SPEC-56's stand-in.

In `merge_characters`, a character left with no verbatim quote always gets
`EVIDENCE_NO_QUOTE` ("no verbatim quote in part N"). "narrates part N" (`EVIDENCE_STAND_IN`) is
printed only on the `style_signals.narrators` path, where it is true by construction. There is no
first-person test. Old Fen in W7 and W7b (one spoken line, not kept verbatim) now reads "no
verbatim quote in part 1"; W7r and W7br keep their quote. **Tests**: `test_extraction_bible.py`.
