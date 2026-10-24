# SPEC-91 — Wave-16 plan: a voice metric that measures, narrator grounding, attribution settled
**Depends on**: SPEC-90 (wave-15 close), SPEC-84. **Approved** by the user on 2026-10-24 ("Approved").

## Items
| # | Item | PR |
|---|---|---|
| W16-1 | R2 grades lines credited from the generator's own speaker per dialogue beat, not from speech tags (Claude writes pronoun tags, so `r2_applicable` was 0.0) | A |
| W16-2 | The narrator role takes only its own narrator's first-person paragraphs (Rowan's 60 chunks); an embedded tale is credited to the "I" who tells it | B |
| W16-3 | Frankenstein attribution re-drawn (run 40074031809, three passes) to settle Rowan vs Emeric; then a topic-neutral probe | — / A |
| W16-4 | The Dracula corpus's leftover "--D." signature renamed (the standing 0.5 continuity false-positive rate) | A |
| W16-5 | Review leftovers: the "said:" colon trigger, era notes on dates inside dialogue, particle-surname owners fused | B |
| W16-6 | The W12-9 rating continues daily at P⁵; score + κ per SPEC-84 at `unrated` = 0 | — |

## Decisions
- **D16-1** Judge-scored draws run on Groq's free tier, at most one a day (its 200k tokens/day are the evals' again: the rating's
  Groq side is complete); Claude judges only as the last fallback (D14).
- **D16-2** W16-1 is measured on the prose step only (about $1.50 a draw): two draws after; before is known (`r2_applicable` 0.0).
- **D16-3** Deferred: a continuity window large enough to hold an era flip.
- **D16-4** Budgets: about 120 test lines, about 120 product lines; spend under the $17.14 left this month (plan ≈ $8).
