# SPEC-79 — Waves 10+11 combined merge gate: the PM's rulings (draws W11, W11b on 875e129)
**Closes**: wave-11 §6.2, the wave-10 hold (SPEC-76) · **Size**: docs · **Depends on**: SPEC-80 (the draw record), -74, -76, -77.
- **Verdict.** The wave-10 hold is released; the combined PR merges **not clean**. (1) met on the "stays" reading; (2) met as worded
  (all four W10 blocks); (3) **not met as written**: B1 is a named exception, B2 a record correction; (4) met (train references; the
  instrument is not validated, SPEC-80). **G-D: read, not met** (0.50, n = 2, thin; the design gate ruled (b)-narrow, SPEC-77).
- **B1 (extraction).** Extraction (W11 `gated-full`) scored 0.741 against the held 1.0 row. Condition 3 is not met on this row, and the PM
  signed it as a named exception under the pm-w9b-signoff §5 rule. The cause is W9-6: `extraction_reduce` 0002 headed Edric's entry with
  the title "the Chief Executive Lich" and demoted "Edric Thane" to an alias. That defect is pre-existing on main: `canon_extractor.py` is
  untouched since main, and the archived W8 draw has the identical output and score. It has no downstream effect. W9-6's wave-9 deferral
  premise, "extraction scores are unaffected", was false when written. W9-6 is a wave-12 P0, with W11's 0002 as its (F) fixture and an
  (R) row. No re-draw was taken, and this merge does not count as clean.
- **B2 (`plan_quotes_source`).** `plan_quotes_source` is reported, not gated, as ruled in wave 8 (Q1) and implemented in SPEC-61 and the
  grader. The "0" target in the wave-9 to wave-11 §6.2 rows, and in the PM's wave-9 held-gate list, was a transcription error against
  that ruling, and is corrected here. Recorded values: W9 1, W10 1, W10b 4, W11 0, W11b 1 (0023 beat 4). The W9–W10b breaches of the
  written 0 passed unremarked at the time. Any future gate on this metric has its bar fixed before that wave's first draw.
- **B3 (condition 1, `samples_not_plot` over 22 archives).** Condition 1 is met on the PM's "stays" reading: no archive's
  `samples_not_plot` moved from main, and the 19 archives that met it on main, W11 and W11b included, remain 1.0 with classified > 0.
  Read literally ("every archive"), condition 1 is not met by three archives that have never met it and are unchanged from main: w4b2 at
  1/15 with classified 77 (SPEC-54), w5b at 19/19 with classified 0, vacuous (SPEC-45, SPEC-54), and w8b at 16/16 with classified 0,
  vacuous (SPEC-66, replaced by W8c).
