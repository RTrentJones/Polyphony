# SPEC-67 — One fair attribution probe: symmetric, masked, k = 10 (W9-2)
**Closes**: W9-2 (a)(b)(c) · **Size**: S · **Depends on**: SPEC-33, -39, -55, -58.
- **(a) One arm.** `step_attribution` sends every character the corpus' `voice_situation` (cel: the PM's colleague-at-your-desk
  text, G2; dracula and frankenstein: their already symmetric probe, copied verbatim). No arm key, no second loop;
  `ATTRIBUTION_DRAWS = 10`. Only the name and voice block differ, in the product's own test-dialogue prompt.
- **(b) Masking** (`evals/graders/attribution.mask`). Masked forms: each cast member's full name, each alias whole, and each
  name's first and last token, with an optional `Mr.`, `Mrs.`, `Ms.`, `Dr.` or `Miss`. Whole-word and case-sensitive on the
  form, so the lower-case "chief" is no name and a title alias ("Chief Executive Lich") is never masked token by token. Each
  match becomes `⟨NAME⟩` in the generated line and every gold test line before embedding. The forms come from the corpus
  (`cast` plus the fidelity principals' aliases); the grader imports no product name rule.
- **(c) Measurement.** Per character, accuracy at k = 10 with a Wilson 95% interval (`per_character`), pooled over the 30
  draws. **Decision rule:** `w9_9_p0` is true when no character's lower bound exceeds 0.33 (at k = 10 that takes ≥ 7/10;
  Wilson 7/10 = (0.40, 0.89)); W9-9 (voice distinctness in prose) then becomes wave-10 P0. Reported, never gated (run-W9a).
- **Unchanged.** SPEC-55's fixed-input probe rows (the product's probe-block selection) and the cel `voice_probe` data.
- **Tests.** (F) one situation × 10 for each character, masked on both sides, `w9_9_p0` false when Milo's bound is 0.72;
  (F) "Mr. Voss, do sit." and "Zara, do sit." both mask to "⟨NAME⟩, do sit." and classify identically; (R) "chief" unchanged.
- **Result (run-W9a2).** 11/30 (chance 0.33): Milo 5/10 (0.24–0.76), Zara 5/10 (0.24–0.76), Edric 1/10 (0.02–0.40). No lower bound clears 0.33, so W9-9 is wave-10 P0, starting by validating the classifier on known-distinct lines.
- **Amendment (W10-5, wave 10).** A name's first token that is also a word (`Old`, `The`, `Mr`, `Mrs`, `Ms`, `Dr`, `Miss`;
  grader-local, since `app.parsing.names` is banned in `attribution.py`) is masked only inside the whole form: "Old Fen said"
  masks, and a sentence-initial "Old habits" is left as it is. Last tokens and whole forms are unchanged. (F) "Old habits.
  Fen?" masks only "Fen"; (R) "Old Fen said so." masks the whole name.
