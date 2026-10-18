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
- **Result.** Recorded here after run-W9a (the orchestrator's run).
