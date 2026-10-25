# SPEC-94 — Wave-19 plan: the study — measure the measurements
**Depends on**: SPEC-84 (W12-9's pre-registration), SPEC-93. **Directed** by the user on 2026-10-25, after an outside review of
Polyphony as evidence of eval engineering: "Go, and approved on those novels" (the holdout titles below), having agreed to
rate the human calibration set ("1. Yes").

## Why
The harness measures a lot, and its numbers have not yet been checked against a person or against books it was never tuned on.
The review's three code claims hold on reading: an unparseable judge reply scores 0.0 (`evals/harness/judge.py`), the
continuity "false-positive rate" divides control findings by the number of injected errors and a finding matches if it merely
mentions a token (`evals/graders/continuity.py`), and the README says renaming means "memorization is defeated". Wave 19 turns
the harness from a development instrument into a study: one finished pre-registered result, the scoring bugs fixed, the
validity claims stated at their true strength, a human calibration of the judges, and a frozen holdout.

## Items
| # | Item |
|---|---|
| S1 | **Finish W12-9 and publish it.** Rate the last prompts (day 6, 10-26), then score and κ exactly as SPEC-84 pre-registered. If `panel-cc97…` fails its second P⁵ run, the "member scores ½" fallback needs a written amendment and the principal's sign-off before any code (SPEC-84's rule). The result is written up whatever its sign. |
| S2 | **Measurement fixes.** (a) An unparseable judge reply is `None` (not scored, counted in the run's `unscored` tally), never 0.0. (b) Continuity reports a false-positive rate per 1,000 control words and the share of control texts with any finding, replacing `control_findings / n_injected`. (c) A finding matches an injection only when its quote overlaps the injected span (or, without a quote, its detail names the replacement), never on a bare token mention. Each fix comes with tests that fail on the old code. |
| S3 | **Validity claims.** The README and docs state that renaming reduces cue-driven recall but does not defeat memorization of famous novels; Frankenstein, Dracula, Wuthering and cel are **development corpora** (tuned on across eighteen waves), and their numbers are labelled so. |
| S4 | **Human calibration set.** About 40 blinded items rated by the user (≈45 minutes) on a private page: 20 R2 speaker-attribution items (the same numbered lines and samples the judge saw; the user names each line's speaker) and 20 scene-preference pairs (the same pair the pairwise panel saw, order randomised). Agreement is computed after rating: human-vs-judge accuracy on R2 and human-vs-panel agreement (and κ) on pairs. |
| S5 | **Frozen holdout.** *The Woman in White* (Collins, 1859) and *The Moonstone* (Collins, 1868) — never used in any earlier wave — built as renamed corpora once, committed, and tagged `holdout-v1`. No development run reads them; they run once, at the end of a release, on that release's frozen code. |
| S6 | **Case study.** A one-page write-up as the README's front door: the problem, the harness, the pre-registered result, the calibration, the holdout, and what did not work (R2 has stayed 0.2–0.4). |

## Decisions
- **D19-1 Order.** The S2 harness change merges only after wave 17's second R2 draw (10-26 05:00Z) is dispatched and after the
  wave-18 product PR (SPEC-93 D18-2), so wave 17's draws and wave 18's before-row run on one harness. W12-9 is unaffected
  either way: it checks out P⁵ (`f57f026`).
- **D19-2 Comparability across S2.** Every judged score already records its reason; a score whose reason is "unparseable judge
  reply" is recomputable as `None` from existing records, so pre- and post-S2 draws are compared with those excluded rather than
  re-run.
- **D19-3 What the holdout is for.** Development corpora have been looked at and tuned against for eighteen waves; any gain on
  them is partly fit to them. The holdout's two novels answer one question: does the result hold on books the system was never
  shaped around? Adding more open-source novels to development would not answer it, because they would be tuned on in turn.
  The holdout is the same kind of book (Victorian, multi-narrator, public domain) so a gap is about overfitting, not genre;
  Collins's novels are told by successive first-person narrators, which suits the narration rubrics.
- **D19-4 Freezing.** `holdout-v1` fixes the corpus files and the build script; the release that is evaluated fixes the code.
  Agents and engineers do not open the holdout excerpts after they are built and checked (the builder's checks are mechanical:
  names renamed, no original name left, word counts). A holdout run that is looked at and then "fixed for" stops being a
  holdout; the next one would need new books.
- **D19-5 Blinding.** The human page shows no system labels; the pair order and item order are randomised by a seeded export
  that writes the items and a separate key. The key is read only by the agreement script, which prints aggregates. Nobody
  (agent or user) reads the key before rating closes. W12-9's keys and record artifacts stay unopened, as before.
- **D19-6 Limits.** The monthly eval total ($40) is the only budget. S5's holdout run and any S4 item generation that needs LLM
  calls run in November unless October's remainder covers them.

## Sequence
1. 10-26 00:07Z — W12-9 day 6 (S1). 05:00Z — W17 draw 2; then the wave-18 product PR.
2. In parallel now, on branches: S2 (code + tests), S3 (docs), S5 (corpus build), S4's export (items + key, no LLM where
   existing records suffice). None merges before step 1's draw.
3. After S2 merges: the S4 page goes up; the user rates; the agreement script runs.
4. S1 result + S4 agreement + S5 holdout run (November) → S6 case study, then this spec's close record.
