# SPEC-93 — Wave-18 plan: write the book — narration and chapters, not only voices
**Depends on**: SPEC-92. **Directed** by the user on 2026-10-25: "Unfreeze prose.py and start wave 18", then: "I want this
to write books - the comparison and evals need to be based not just on characters but on narration as a whole too" (D18-0).

## Why
W17-1 changed only the cast context (honest narration label, a computed per-beat voice contrast); Frankenstein's R2 did not
clearly move (0.42 against wave 16's ≈0.42), and the judge still reads both speakers as "terse, plain, unlike the long
ornate samples". The writer's own instruction (`write_beat_prose`, `app/orchestration/prose.py`) asks for distinct voices
only generically; it was frozen since wave 13 (the seam gate).

## Items
| # | Item |
|---|---|
| W18-1 | `write_beat_prose` names each beat speaker and requires their lines to follow their own `<character>` block (and the `<contrast>` line) rather than a shared narrative register; the house "dialogue punctuates" guidance no longer clips a speaker whose samples are long and periodic |
| W18-2 | The ensemble agent's `<voice>` block opts into W17-1's narration split (`context.py`; `ensemble.py` stays frozen) |
| W18-3 | The Dracula corpus's "Transylvanian" renaming leftover |
| W18-4 | Draws after the wave (Groq-judged), against W17's, on the new narration and chapter scores as well as R2 |
| W18-5 | **The book's own narration is the default style reference.** A book with no style samples gets prose excerpts from its own source's narration (outside quotes) as the writer's style samples, instead of the generic house craft guidance (Frankenstein has no `style.json`, so its writer never saw Shelley's narration) |
| W18-6 | **RN, narrative voice (per scene):** with dialogue removed, does the narration match the source's narrator — POV person, tense, register, diction, period, sentence rhythm — judged against source narration excerpts |
| W18-7 | **RC, the chapter as a whole:** one judge call reads the whole generated chapter: does it read as one chapter of this book — scene-to-scene transitions, pacing, no repetition across scenes, one consistent narrator, the outline chapter's change lands |

## Decisions
- **D18-1 Unfreeze.** `app/orchestration/prose.py` leaves the seam gate (the user's direction); `ensemble.py` and
  `app/core/config.py` stay frozen. The gate's seam row is amended in the same change, and the W12-9 rating is unaffected
  (it checks out P⁵, whose `app/` is `e428c77`).
- **D18-2 Order.** The product PR merges only after wave 17's second R2 draw (scheduled 10-26 05:00Z) is dispatched, so that
  draw stays W18's before-row.
- **D18-4 Scores.** RN and RC are reported beside the scene score and as a `book` block (RN mean, RC), not folded into
  `scene_score`, so the prose score's history stays comparable; a composite can come once both have a few draws.
- **D18-5 Comparison** across releases at the chapter level (a blind pairwise preference between this release's chapter and
  the last one's, like SPEC-84's panel) is the next step after RN/RC exist; proposed for wave 19, not built now.
- **D18-3 Limits.** Only the monthly eval total ($40); line counts are reported, not gated (the user, 2026-10-25). W18-4's
  draws likely run in November.

## W18-4 after-draw (run 40453584712, main `a2f3039`, 2026-10-27; Claude-written; judged Groq then Sonnet after Groq's limit)
| book | R2 | applicable | lines | RN mean (scored/eligible) | RC |
|---|---|---|---|---|---|
| dracula | 0.35 | 4/4 | 19 | 0.33 (4/4) | 0.72 (trimmed) |
| frankenstein | 0.56 | 2/4 | 9 | 0.44 (4/4) | 0.68 (trimmed) |
- **Against wave 17** (draw 1: Frankenstein 0.42, Dracula 0.24; draw 2: Dracula 1.0 on one scene, RN 0.27, RC 1.0): R2 reads
  higher on both books, but this draw's judges were mixed (Groq, then Sonnet), it crosses the opaque-titles change (SPEC-94
  D19-10), and no test-retest spread exists yet (SPEC-95 E1). Not read as a gain until E1's noise floor and E4 say so.
- **W18-5 did not take effect on person.** Every RN explanation says the scenes are third-person past while the source narrator
  is first person (Aldous Kerr's journal; Rowan Merrow's letters). The eval books set no POV, so `_style_section` tells the
  writer to take person from the excerpts, but the beat prompt also prints `pov_instruction`'s default "Point of view: third
  person limited", and the model follows the explicit line. Product fix carried to the next wave: when `person_from_narration`
  holds, the POV line must not name a default person (state the person the excerpts use, or none).
- **RC** names real defects in both chapters: a repeated beat across scenes (both books) and a first-person slip in
  Frankenstein ("I feared, Rowan thought").
- **Spend:** $4.67 (Dracula $1.54, Frankenstein $3.13), counted in SPEC-95's ledger.
