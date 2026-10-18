# SPEC-74 — Validate the voice instrument first; one length limit (W10-2)
**Closes**: W10-2 (a)(b); (c) skipped by rule · **Size**: S · **Depends on**: SPEC-33, SPEC-55, SPEC-67.
- **(a) The instrument** (`evals/measure/voice_instrument.py`, offline; `python -m evals.measure.voice_instrument`).
  Leave-one-out over the 22 cel gold lines (Milo 11, Zara 6, Edric 5; 15 train, 7 test), names masked as
  `step_attribution` masks them, classified by the unchanged nearest-centroid `attribution.attribute`. References are
  **train lines only**: a test line is scored against every train line, a train line against the other train lines
  (`references(gold, held)`). A test line is never a reference. No new classifier. **Validated** at >= 18/22 with each
  principal >= 0.5. The 30 W9a2 answers (written distinct) are scored against every train line, reported only.
- **Result: UNVALIDATED.** 12/22 (Wilson 0.35–0.73): Milo 5/11, Zara 3/6, Edric 4/5. Milo is under 0.5 and the total is
  under 18. The recorded baseline is that score. W9a2's answers: 22/30 (chance 0.33, mean gap 0.087), against 11/30 from
  run-W9a2's own grade, whose references are the test lines. **G2: run-W10a is skipped.** Voice stays wave-11 P0, with the
  instrument as its first deliverable.
- **(b) One length limit.** The probe template (`app/characters/dialogue.py`) reads "Keep it to one to three sentences",
  the same words as cel's `voice_situation` (PM, "Answer in one to three sentences."). There is no vocative filter. SPEC-55's
  probe-block rows are unchanged.
- **Tests.** `test_voice_instrument.py`: (F) references are train lines less the held line; (F) 18 correct with one
  principal at 0/4 is unvalidated, and at 3/4 it validates; (R) the score and the answers' accuracy are recorded either
  way. `test_dialogue_probe.py`: (F) the probe prompt's two length phrases are both "one to three sentences"; (R) the
  SPEC-55 rows are unchanged.
