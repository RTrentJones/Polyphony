# SPEC-85 — Attribution: the topic-controlled control (wave 12, W12-6 (a); amends SPEC-74, -80)
**Closes**: W12-6 (a) · **Size**: S · **Depends on**: SPEC-74 (the instrument), SPEC-80. (b), raters and κ, is wave 13.
- **Set.** `evals/corpora/cel/topic_controlled.json`: 10 fixed events × the 3 principals = 30 lines, written by a separate
  authoring agent from the synopsis and character bibles only (no gold lines, no instrument); gold lines are not quoted.
- **Mode.** `python -m evals.measure.voice_instrument --topic`: each line against every train line through the probe path
  (`probe_path`: `attribution.accuracy` plus `wilson`, shared with the positive control). Beats chance when the Wilson
  lower bound is > 1/3 (16 of 30). (F) `test_topic_mode_scores_the_probe_path_against_chance`: an embedding that
  carries the event, not the speaker, sits at chance (10/30).
- **Result, `dev` (embedder, $0):** 18/30 = 0.60, Wilson [0.42, 0.75]: **beats chance** (0.42 > 0.33). Per principal:
  Milo 8/10, Edric 8/10, **Zara 2/10** (5 to Milo, 3 to Edric). Caveat: the author kept Edric free of contractions, a
  voice trait and also a surface cue; without Edric it is 10/20, Wilson [0.30, 0.70], **not** above chance. It does not
  track topic alone, but the margin over chance is Edric's no-contraction cue, a voice trait that is also a surface cue;
  on Zara it is below chance (2/10). Attribution stays reported, never gated, until (b) validates it (κ ≥ 0.4 and this
  control).
