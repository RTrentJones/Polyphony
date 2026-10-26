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

## W17-4 draw 2 (run 40272098890, `5bb2df2`, 2026-10-26, Groq-judged; wave 18's before-row)
| book | R2 | applicable | RN mean | RC | scenes (R2 / RN) |
|---|---|---|---|---|---|
| frankenstein | — (step failed) | — | — | — | — |
| dracula | **1.0** (one scene) | 1/3 | 0.27 | 1.0 | 1.0 / 0.0, n/a / 0.4, n/a / 0.4 |
- **Frankenstein failed after its prose was written.** Groq's 8,000-TPM limit outlasted the client's 90 s of 429 retries, and the
  judge re-raised a per-minute `QuotaExhaustedError` (only a daily cap failed over, D14), so the whole prose step errored. Fixed on
  the wave-19 branch: a per-minute limit now waits a minute on the same judge, twice at most, before failing over.
- **Dracula's R2 rests on one scene.** Two of three scenes had under two cast speakers with voice samples (not applicable), so
  1.0 is a single scene's reading and does not compare with draw 1's 0.24 over four scenes.
- **RN, first live reading (W18-6).** 0.0–0.4 per scene: every explanation names the same gap — the generated narration is
  third person where the source narrator (Aldous Kerr's journal) writes in the first. RC read the chapter as coherent (1.0). This
  is the before-row for W18-5 (the book's own narration as style reference, taking person and tense from it when no POV is set).
- **Judge:** Groq throughout for Dracula; no failover recorded.
- **Spend:** $3.73 for this draw (Frankenstein $2.49, mostly generation before the failed judging; Dracula $1.24). `evals.spend`
  read $31.78 spent before it, so October stands at ≈ $35.51 of $40 (draw 1's "$33.37" above counted an unsettled ceiling).

## Close record (2026-10-26)
- **W17-1** (honest narration label, per-beat voice contrast in the cast context) shipped; Frankenstein's R2 did not clearly
  move (draw 1: 0.42 against wave 16's ≈0.42), and draw 2 could not re-measure it. D17-1's condition is met; the user unfroze
  `prose.py` for wave 18 (SPEC-93 D18-1).
- **W17-2/3** (empty-claim detector, Vasska's gold lines) shipped; Dracula's R2 applied for the first time in draw 1.
- **Carried to wave 18:** the ensemble agent's `<voice>` block (W18-2) and Dracula's "Transylvanian" renaming leftover
  (W18-3, then the fuller wave-19 sweep, SPEC-94 D19-10).
- **Measurement carried to wave 19:** a single-scene R2 is too thin to compare draws on; S8's evidence counts make that visible,
  and the per-minute judge fix keeps one busy minute from losing a book.
