# SPEC-74 — Validate the voice instrument first; one length limit (W10-2)
**Closes**: W10-2 (a)(b); (c) skipped by rule · **Size**: S · **Depends on**: SPEC-33, SPEC-55, SPEC-67.
- **(a) The instrument** (`evals/measure/voice_instrument.py`, offline; `python -m evals.measure.voice_instrument`).
  Leave-one-out over the 22 cel gold lines (Milo 11, Zara 6, Edric 5; 15 train, 7 test), names masked as
  `step_attribution` masks them, classified by the unchanged nearest-centroid `attribution.attribute`. References are
  **train lines only**: a test line is scored against every train line, a train line against the other train lines
  (`references(gold, held)`). A test line is never a reference. No new classifier. **Validated** by the wave-11 rule
  below (both controls).
- **Result: UNVALIDATED.** 12/22 (Wilson 0.35–0.73): Milo 5/11, Zara 3/6, Edric 4/5. (Read in wave 10 against a since-replaced
  18/22 bar.) The recorded baseline is that score. W9a2's answers: 22/30 (chance 0.33, mean gap 0.087), against 11/30 from
  run-W9a2's own grade, whose references are the test lines. **G2: run-W10a is skipped.** Voice stays wave-11 P0, with the
  instrument as its first deliverable.
- **(b) One length limit.** The probe template (`app/characters/dialogue.py`) reads "Keep it to one to three sentences",
  the same words as cel's `voice_situation` (PM, "Answer in one to three sentences."). There is no vocative filter. SPEC-55's
  probe-block rows are unchanged.
- **Tests.** `test_voice_instrument.py`: (F) references are train lines less the held line; (R) the leave-one-out
  is recorded (18 or 21 by one principal's side); the verdict is the wave-11 rule's. `test_dialogue_probe.py`: (F) the probe
  prompt's two length phrases are both "one to three sentences"; (R) the SPEC-55 rows are unchanged.
- **Note (principal F3).** W9a2 answers vs train refs 22/30, 0/30 lifted; wave 9's 11/30 used test-line refs; the
  leave-one-out failure is reported, and validation is redesigned in wave 11.
- **Amendment, wave 11 (D4).** (a) `step_attribution`'s centroids are the **train** lines; the report says `reference: "train"`
  and no test line is embedded. (b) `instrument = {positive, permutation_p, beats_shuffles, self_split, validated}`:
  **positive**, the 30 W9a2 answers (`evals/corpora/cel/w9a2_answers.json`) against every train line, with its Wilson interval;
  **negative**, the leave-one-out over `SHUFFLES = 200` label shuffles from the pre-registered `SEED = 11` (each line keeps its
  side, each speaker its count), `permutation_p = (1 + shuffles scoring >= the true score) / 201`; the **self-split**, each
  principal's lines alternately as two speakers, reported, never gated. **Validated** = the positive Wilson lower bound > 0.33
  and the true score beats >= 95% of the shuffles (`beats_shuffles`). Result: positive 22/30 (0.56-0.86); p = 0.1194, beats
  0.885: **not validated**, so run-W11a is skipped (numbers in SPEC-79). Tests: the LOO reported on train refs (R); the
  permutation on separable and flat vectors (F); validated needs both controls (F); the self-split reported, and validation
  does not read it (F).
