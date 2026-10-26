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
