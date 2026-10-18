# SPEC-75 — The gate definitions are fixed before any draw (W10-3)
**Closes**: W10-3 (a)(b)(c), T8–T10 · **Size**: S · **Depends on**: SPEC-62 (D4), SPEC-68 (preflight), SPEC-69.
- **(a) Prose is chapter 1.** `pipeline._planned_scenes` generates the first promoted chapter's planned scenes whatever its
  cast; the "fewer than two principals" skip is gone, and a chapter 1 that cannot be generated fails the step (no other
  chapter is tried). The report records `steps.prose.prose_chapter` (position + 1); §6.2 gates it `== 1`. W9b's prose had
  silently run chapter 2. `PROSE_CACHE_KEY` is `planned-v10`.
- **(b) The drawn chapter.** D4's "cast in a chapter-1 block" becomes "cast in a block of the drawn chapter": the one
  STORY_POSITION chapter of the run's beat_prose records. Records spanning more than one chapter raise `ValueError`.
- **(c) The named exemption.** `secondary_has_context.exempt` is `"the drawn chapter's plan names no secondary"` only when
  n = 0 and the drawn chapter's archived outline:beats nodes (matched by `<chapter>` summary; chapter and beats `people`
  and `secondary_names`) name no one outside the principals; otherwise `null`, and n = 0 fails. `evals.gate.exercised`
  (CLI `python -m evals.gate exercised <report>...`) fails "not exercised" unless the check ran (n > 0) in one gated draw.
- **Regrade (18 archives).** Only W9b changes: `secondary_has_context` goes from an n = 0 failure to exempt, and movement's
  `not_cast` drops Milo and Zara (Zara is cast and starved); `ok` stays false. The other 17 gain only `exempt: null`.
- **`RUNS` minimums** (`evals/preflight.py`, day-0; json chars / 4 × 1.3, rounded up to 10k): `gated-full` 340,000 from
  W9 247.6k + 11k (W10-1's source ingest and check call); `gated-scenes` 320,000 from W9b 228.3k + 11k; `attribution`
  40,000 (W9a2 24.3k, unchanged).
- **Tests.** (F) `test_prose_is_pinned_to_chapter_1_on_the_w9b_shape`, `test_movement_reads_the_drawn_chapters_cast`,
  `test_beat_prose_spanning_two_chapters_has_no_drawn_chapter`, the quiet-plan exemption and
  `test_both_draws_exempt_is_not_exercised`; (R) a plan naming Prava is not exempt; the W8c/W8r/W9 regrade rows unchanged.
