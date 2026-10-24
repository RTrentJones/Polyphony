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
- **D16-5** W16-1 (the user, 2026-10-24: "Judge assigns speakers"): the product's prose path records no speaker per line
  (`write_beat_prose` writes every voice in one call; `SceneBeat.dialogue` is never written), so before R2 the eval's judge
  (same chain, `Judge.ask_json`) names the speaker of each quoted line the tags left uncredited: one cast name or "other"
  (unanswered or unknown = "other", excluded). Tags stay the first pass; each scene records `r2_source` (tags | judge |
  mixed). Rejected: unfreezing `prose.py` to emit attributed dialogue; crediting single-voice beats from the grounding cast;
  deferring W16-1.

## Results
- **W16-3 re-draw** (run 40074031809, frankenstein, polar-ship probe): passes 0.533 / 0.633 / 0.433, mean 0.533 ± 0.082
  (chance 0.333). No regression: the post-PR-B 0.40 / 0.27 were low draws within that spread. Last pass per character: Rowan
  9/10, Emeric 4/10, the Creature 0/10 (normalised 0.567: 6 / 7 / 4). Frankenstein now opts into a topic-neutral probe set.
- **D16-4 amendment.** PR B's three review rounds (an addressed bystander taking a chapter; "said:" tales as dialogue; the
  letter-signature rule's crash, initial-only and quoted-letter cases) bring `app/` to +147 and the wave's tests to +194
  (PR A 78, PR B 116); the user accepted the overrun on 2026-10-24 ("Accept the overrun").
- **First R2 draw** (run 40081633631, prose only, `4763272`): Frankenstein R2 applies in 3 of 4 scenes (25 lines) and
  scores **0.31**: the first real voice measurement, and a low one. Dracula: 0 applicable scenes (no scene had two cast
  speakers with voice samples). The judge chain fell through to Claude (labelled). Spend $3.25 (month: $26.08 of $40).
