# SPEC-92 — Wave-17 plan: characters that sound like themselves
**Depends on**: SPEC-91 (wave-16 close). **Approved** by the user on 2026-10-25 ("Go").

## Why
R2 (dialogue voice distinctness) applied for the first time in wave 16 and scored low on Frankenstein (0.31 Claude-judged,
0.21 Groq-judged). The investigation: Rowan and Emeric have no speech chunks, so every "voice sample" the writer sees is
first-person narration labelled "their own words", and the writer gives both the same clipped modern register; the judge
expects the samples' ornate diction. One Groq scene scored 0.0 on "No dialogue provided" despite 9 lines (wave 15's
empty-input detector wants a verb). Dracula's R2 never applies: Count Vasska, in its first two-principal scene, has no gold lines.

## Items
| # | Item | PR |
|---|---|---|
| W17-1 | Narration used as voice evidence is labelled honestly ("how they write: their speech keeps this diction, sentence length and period"), and each beat gets a one-line computed contrast of its speakers (sentence length, exclamations, archaic forms), no LLM call (`app/characters/context.py`) | B |
| W17-2 | The R2 empty-input detector also catches "No dialogue provided" (no verb) | A |
| W17-3 | Count Vasska gets gold lines from his own speech in the Dracula excerpt, so R2 can apply | A |
| W17-4 | Two prose-only R2 draws on Groq, against wave 16's two | — |
| W17-5 | The W12-9 rating finishes; score + κ to the principal and the user | — |

## Decisions
- **D17-1** `app/orchestration/prose.py` stays frozen; unfreezing is a wave-18 decision if W17-1 does not move R2.
- **D17-2** No Frankenstein `style.json` and no cast change (they tune the eval's own inputs); Vasska's gold lines fill a
  ground-truth gap from his real speech.
- **D17-3** Two prose draws ≈ $8–9 against $11.24 left in October; otherwise November.
- **D17-4** Budgets: ≈ 60 product lines, ≈ 60 test lines.

## PR B review (2026-10-25)
- Blocking: sentence counts split at "Mr."/"Dr."/"St."/"p.m." and ellipses (a formal Victorian speaker read as clipped) —
  titles, a.m./p.m. and ellipses no longer end a sentence. Should-fix: "shall"/"whilst" off the archaic list. The narration
  label is soft ("lean toward…") when the character also has speech samples, strong only when narration is all there is;
  contrast numbers are coarse (words/sentence to the nearest 5, rates to one decimal).
- Known gap: the ensemble agent's `<voice>` block still prints own narration as quoted speech, with no label or contrast
  (it does not opt in to the split); fixable in `context.py` without touching the frozen `ensemble.py` — wave 18.
- **D17-4 amendment.** Product +95 added against ≈60; the user accepted the overrun on 2026-10-25 ("Accept").

## Standing decision (the user, 2026-10-25)
"I'm not worried about [the per-wave] budget, just monthly total." From wave 17 on, product and test line counts are
reported, not gated or put to the user; the only spend limit is the monthly eval total ($40, D1).

## W17-4 draw 1 (run 40161986625, `86123fd`, judged Groq then Claude — labelled mixed/self)
| book | R2 | applicable | lines | scenes |
|---|---|---|---|---|
| frankenstein | **0.42** | 3/4 | 18 | n/a, 0.40, 0.45, 0.40 |
| dracula | **0.24** | 4/4 (first time: Vasska's speech lines) | 20 | 0.0, 0.0, 0.40, 0.55 |
Against wave 16 (Frankenstein 0.31 Claude-judged; 0.21 Groq, ≈0.42 without its "No dialogue provided" 0.0), Frankenstein has
not clearly moved: the judge still reads the lines as "terse, plain, unlike the long ornate samples". One Dracula 0.0 is
another empty claim ("No dialogue was presented"), so the detector also takes presented/included/shown. Spend: $4.61
(month $33.37 of $40). Draw 2 runs on Groq alone once its daily tokens recover.
