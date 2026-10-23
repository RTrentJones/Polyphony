# SPEC-90 — Wave-15 plan: an honest Claude eval, then the extraction and ingestion it exposes
**Depends on**: SPEC-84, SPEC-89, BRD R7.9. **Approved** by the user on 2026-10-23 ("Approved, separate budget, and your
recommendation"). Results and counts land here as the wave closes.

## Why
Run 39920544701 (Frankenstein, `e1aba3d`, generator `claude-opus-5-5`, judge Groq) was the first eval to score every step;
the Gemini free tier never finished one. Two read-only investigations split its low scores in two:

- **Harness.** The Groq judge sometimes answers "no artifact/dialogue was provided" on input it was given (outline R3 = 0.0
  while R1 and R6 scored 1.0 from the same body; two prose scenes whose quoted lines `_QUOTE` does match). R2 also grades
  every quoted line, crew included, against the scene cast's voice only, and has no not-applicable path. Attribution's one
  probe is on two characters' topic, so The Creature ranks last on all 30 probes, its own included.
- **Product.** Extraction ranks a canonical name by token count (`canon_extractor.py` `_fold_group.rank`), so
  "Robert (not used)" outranks "Rowan Merrow" and "my dear sister" outranks "Isolde Sarrow"; nothing strips parentheticals;
  merge rule 3 lets "the stranger" join Emeric and Soraya. Ingestion reads a Victorian nested narrative (each paragraph
  opens a quote, only the last closes) as one uncredited speech and withholds it, so Fenwick and Alina get 0 chunks; the
  pronoun alias "I" inflates The Creature. Continuity misses an era flip in the first chunk (each chunk sees only its
  predecessor's tail; the fact sheet has no era).

## Items
| # | Item | PR |
|---|---|---|
| W15-0 | Eval budget guard: a `claude=yes` run sums this month's eval spend from earlier runs' report artifacts and caps itself at what is left of the monthly budget | A |
| W15-1 | Pre-fix Claude draw, all three books, at `a52817c` (`app/` = `e1aba3d`) | — |
| W15-2 | A judge answer claiming nothing was provided is retried once, then recorded as `None`, never 0; outline R3 gets a body that labels PLAN and TEXT | A |
| W15-3 | R2 grades only lines it can attribute to cast speakers; not applicable (`None`) under two cast speakers with samples; the report keeps the spoken lines | A |
| W15-4 | Attribution scores normalised by each centroid's own baseline similarity (topic control) | A |
| W15-9 | The panel-memo (F) row owed since wave 14 | A |
| W15-5 | Extraction names: `name_core()` (parentheticals and leading determiners/possessives stripped; no capitalised token = descriptor), rank on proper-name tokens, rule 3 links only on proper-name tokens, map prompt tightened; BRD R4.4 | B |
| W15-6 | Nested-narration ingestion; pronoun aliases rejected | B |
| W15-7 | Continuity era ledger: a deterministic pass over the whole source flags date outliers | B |
| W15-8 | Post-fix Claude draws, two per book, against W15-1 regraded under PR A | — |

## Decisions
- **D1 Budget.** Evals get **$40/month**, separate from the $50 prod ceiling (the user's choice). Each eval run boots an empty
  database, so the app's ceiling cannot see the month; W15-0 does. One book costs about $2.60 with Opus. The Anthropic
  console's spend limit is the hard backstop. Per-run caps are no longer the control; the monthly total is.
- **D2 Measure before fixing.** W15-1 runs before any change, so a better score after can be attributed.
- **D3 Harness before product.** PR A merges first and W15-1 is regraded under it, so PR B is judged by an instrument that
  does not zero scores at random.
- **D4 Groq stays the judge.** A Claude judge would grade Claude's own writing (self-grading, which the harness already
  labels). If "nothing provided" persists after W15-2, raise Groq's reasoning effort to medium (free) next.
- **D5 Opus stays the generator.** Sonnet halves the cost but changes the model under test; a Sonnet comparison is its own draw.
- **D6 Comparability.** R2 and the outline score change meaning; `evals/history.csv` rows before PR A are marked as graded
  by the old harness. The act rule's `r2_mean` is reported, never gated (SPEC-58). Nothing frozen by SPEC-75 or SPEC-84
  changes, and the W12-9 panel (P⁗) is untouched.
- **D7 Names in code, not only the prompt.** The descriptor ranking would win against any model that emits one descriptor.
- **D8 Still deferred:** W14-9, W14-12 (N1/N2), W14-14. None shows in the Claude scorecard.
- **D9 Counts.** Test backstop **300** lines for the wave (the user took the recommendation): PR A ≈ 140, PR B ≈ 160.
  Product cap **160** lines (PR B's three fixes; PR A changes `evals/` and YAML only).
- **D10 Codex.** Per the user (2026-10-23), small targeted fixes merge on a green gate and CI without waiting for a
  Codex re-review; findings that arrive later are still answered.

## PR A review (coordinator's decisions, 2026-10-23)
- **B1.** "Nothing provided" is a claim only at a score <= 0.1 with the tight, word-bounded phrase; three false-positive sentences are test rows.
- **B2.** Each attempt uploads its own report artifact with `eval-ceiling.json`; a run killed before usage counts at its ceiling; Claude runs share one never-cancelled concurrency group.
- **N1.** Reports record `mode` (boot | external); the budget counts boot-mode reports only.
- **N2.** Attribution's score stays the raw accuracy; `normalised` is reported beside it, baseline = mean similarity to the other characters' generated lines.
- **N3.** Prose reports `r2_applicable` and `r2_lines` beside `rubric_means`.
- **N4.** Consecutive spans by one speaker in a paragraph join into one R2 line.
- **N5.** The guard refuses under $3 left (one book); report artifacts are kept 90 days.
