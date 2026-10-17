# SPEC-56 — Extraction evidence is the author's text, or nothing

**Closes**: W7-4 (a) (Q13) · **Size**: S · **Depends on**: SPEC-17, -26; day-0's `tag_spans`.

## Design
`canon_extractor.verbatim_evidence(quotes, window)` runs on each map answer, against the window the
model read. Each quote is:
- **kept as written** if it is a run of the window, after whitespace and quote-mark normalisation
  and with its own outer marks aside;
- else **replaced** if its words (in any case) are a run of the window's words once the `tag_spans`
  clauses are left out. The replacement is the author's span from its first word to its last, tags
  included, widened over an adjacent opening `"` and a closing `.!?`/`"`;
- else **dropped**.

A member left with no quote gets `"narrates part N"` at merge. A checkpoint's finished windows keep
their saved quotes.

## The fixture's two noted rows
- **"she said, warmly"** is **dropped**. Its `tag_spans` clause is "she said" only, so ", warmly."
  is narration between the halves. Widening the rule here would restate the tag clauses.
- **"Mr. Voss, do sit."** is **repaired** to `"Mr. Voss," the Lich said. "Do sit.`. Joining across
  a tag lower-cases the second sentence's first word, so words match in any case. The same match
  repairs quotes that differ from the source only in punctuation or case ("by lunch Halvard Bell…").

**Rows** (`w6_archives.json`, all 80 lines): 60 kept (every `verbatim`), 14 repaired (5 `tag_split`,
9 `paraphrase`), 6 dropped. Every remaining line is a run of its window.
**Tests**: `test_extraction_bible.py`.
