# SPEC-71 — Wave-9 merge gate record (§6.2 draws W9 and W9b)
**Closes**: §6 of the wave-9 plan · **Size**: docs · **Depends on**: SPEC-40, -67, -68, -69.
- **Draws** (79beb85, each preflighted as a declared run): W9 `gated-full`, W9b `gated-scenes`, W9a2 `attribution`.
  W9a was voided: its first synchronous voice test timed out before the relay's model agent was polling.
- **W9 passes every row:** placement signed; movement 14/14, `ok` true (shown Milo 14, Zara 8; Edric not cast);
  `voice_coverage` 1.0 (49 samples); `samples_not_plot` 18/18, classified 75; evidence 37/37; tighten clause 3/3; empty [].
- **W9b: not passed, merged on the PM's sign-off.** Movement 7/16, `ok` false (Zara starved in 9 chapter-2 blocks,
  "withheld (later_plot, restates)"), and `secondary_has_context` n = 0 with no exemption named in advance. Chapter 1
  cast one principal, so prose ran on chapter 2, which plans no secondary.
- **Cause (pre-existing).** The outline plans Bell twice; `later_plot_from` scores p9 (the Bell account) 7 against ch4
  "Wellness Check" (Bell alive) and 5 against ch2, the margin all names and biography, so chapter 2's cut falls at p9
  and hides Zara's p11 and p29. `later_plot_from` is unchanged since wave 8, `_withheld_by` is behaviour-identical, and
  the 16-archive regrade is identical on 1cc4bc2 and fb7d3a7.
- **PM sign-off:** "merging ships no new defect; it ships the eval that found this one." No redraw and no fitted rule.
- **Wave-10 P0:** placement scores event words, not names (swept over all 17 outlines, W9b as the (F) fixture);
  W9-4 (outline against source) with its eval side first; voice distinctness, starting by validating the classifier.
  Gate changes: prose pinned to chapter 1 with `prose_chapter` reported; a named exemption for `secondary_has_context`
  when the drawn plan names no secondary. The PM will not sign a third consecutive exception for the same cause.
- **§6.3:** tighten RA means 0.90 and 0.817 (min 0.75): no fire. R2 means 0.857 and 0.888 are both < 0.90, so the act rule fires on R2 (voice), folded into W9-9.
