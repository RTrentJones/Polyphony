# SPEC-73 — The outline against the author's source (W10-1 (b), option B)
**Closes**: W10-1 (b), with the PM amendment · **Size**: M · **Depends on**: SPEC-02, SPEC-08, SPEC-12, SPEC-31.
- **Call.** After a staged outline's S4 audit, the outline job reads the book's live sources (`continuity.source_texts`) and
  makes **one** `continuity.check_chunk` call (`staged_outline.check_against_sources`). SPEC-31's prompt and
  `validate_findings` are unchanged. The chunk (`outline_chunk`) is every chapter summary (its scene 0) and scene summary
  under its `SCENE_MARKER`; a quote locates to its node's title. The fact sheet is the sources, fenced as `<source>`, under
  `SOURCE_FACTS_NOTE`: "The author's own pages: what they establish is fact. For each contradiction, say in the detail which
  fact of these pages the outline contradicts." The PM amendment lives in that sentence; compliance is model output.
- **Warnings** (warn-only, on `plan.warnings`): `OUTLINE_SOURCE_WARNING` + detail + ` ("<outline quote>")`; an unlocated quote
  reads ` (could not locate "<quote>" in the outline)`. An unparsed answer gives "Outline not checked against your source"; a
  prompt over `MAX_CANON_CHARS × 2` gives "…: too long" and no call. Neither carries the prefix.
- **Bounds.** No source, no call; no call in `single` mode or at the premise stop. The result is checkpointed under
  `source_check` (keyed by the input hash), so a resume never repeats it. Outline prompts are untouched: a book without
  sources sends byte-identical prompts and nothing more.
- **Estimate.** `estimates.outline(canon_chars, source_chars, staged)`, `source_chars` required: a sourced staged outline is +1 call.
- **Stub.** Answers a `<source>` check with no findings. The PM's "stub-LLM row" is a canned model answer in the unit test's
  `FakeLLM`, not detection logic in `stub_llm.py` (principal ruling, E2 checkpoint).
- **Tests.** `test_staged_outline.py`: (F) W9b + cel, a canned finding stating the gold fact: one JSON call, the fact sheet in
  the prompt, the prefix, located at "Wellness Check"; (R) no source, no call; (F) "could not locate", unparsed, too long; resume.
  `test_continuity_v2.py`: live sources, oldest first. `test_estimates.py`: the call is counted. Journey: one check per outline.
