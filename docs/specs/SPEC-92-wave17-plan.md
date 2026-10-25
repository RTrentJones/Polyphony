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
