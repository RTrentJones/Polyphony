# SPEC-97 — W21-C: does the POV fix matter when Claude writes?
**Depends on**: SPEC-96 (wave 21: the arms, the harness at cc34b82 merged as ebed0aa, M1's validation, the analysis),
SPEC-95 (the ledger). **Directed** by the user on 2026-10-31: "We have $50 in credits still remaining to use by Nov 19 -
work on the experiment plan and execute with that new budget".

## Why
Wave 21 tests three levers with Flash-Lite, the product's default writer, which is free and limited by its daily quota,
not by money. The Claude credit buys a question the free quota cannot: whether the leading candidate product change, the
POV fix (arm P: a book whose style guide names no POV mode is no longer told "third person limited"), matters when
Claude writes, as it does for the eval users the app lists for Claude. A cost pilot (run 41247908629, arm A on Dracula,
Claude-written, read for cost and counts only and excluded from every analysis) cost $2.25 for six chapters, 6/6
written. At about $2.33 a run with judging, the money buys one contrast at a useful n, not four arms, so W21-C is A
against P only.

## Design
- **Arms.** A (no ablation) and P (`ablation=pov_fix`), both `claude=yes claude_generate=yes judge=claude`, on main with
  `app_sha=ebed0aa1a0c5b1e28da30b927ac294f88aa70cd8` (SPEC-96's signed harness), the same three fixed outlines (Dracula
  40651541095, Wuthering Heights 41125885372, Frankenstein 40651268575), `chapters=6 replicates=1`. The writer is recorded
  in each report's `generator`.
- **Units and days.** One unit (A and P on one book, then `narration_judges` over the two runs) per Pacific day, after
  the day's W21 work, starting the first Pacific day after the signature. Books rotate D, W, F; the first arm alternates
  by day starting with A (day 1 D: A, P; day 2 W: P, A; day 3 F: A, P; day 4 D: P, A; …). Dispatches are one at a time,
  each after the previous has completed (claude=yes runs share one concurrency group). Each unit carries the ledger label
  "W21C day d"; pairing is by that label.
- **Stop.** The earlier of: the next unit would take W21-C's spend past its cap of $32 (≈ $4.7 a unit), or the end of
  Nov 16 Pacific. Judging finishes by Nov 18; the analysis runs once, with W21's, by Nov 19. Decided by date and spend,
  never by outcomes. At $4.7 a unit the cap allows six units (two per book: 17 clusters, about 34 pairs).
- **Budget (D20-1's ledger).** Spent so far $15.81 (including the pilot). W21's remaining judging ≈ $8, E4's last three
  compares ≈ $1.7, W21-C ≤ $32, reserve ≈ $6: total ≤ $64.

## Pre-registration (signed by the principal before any W21-C data)
- **Hypothesis.** H1c: with Claude writing, P changes `person_mismatch_window` against A, with the direction required to
  be lower. One primary test; no multiplicity correction.
- **Statistic.** SPEC-96's: per-cluster (book, outline position) mean paired difference (P − A), exact two-sided
  sign-flip test on the cluster means, t-interval reported, percentile bootstrap descriptive. Fewer than 7 clusters at the
  stop: "undetermined: too few clusters". The analysis runs `python -m evals.narration.analyze` with `--app-sha
  ebed0aa1a0c5b1e28da30b927ac294f88aa70cd8` and a W21-C-only `--days` file; only the P contrast is read, and its raw
  sign-flip p is the test (the tool's N and V lines have no pairs and are not part of W21-C).
- **Room to move (read at analysis).** If A's mean `person_mismatch_window` has a t-interval whose upper bound is below
  0.05, H1c reads "no room: the Claude writer already holds the book's person under A", with no product inference.
- **Descriptive.** M2 (narrator attribution, Sonnet and Groq), M3 (speaker attribution), sentence-level person and
  tense mismatch, per-arm failures, and the same measures for W21's Flash-Lite A and P beside them (unpaired across
  writers, descriptive only).
- **Exclusions.** As SPEC-96. The cost pilot (run 41247908629) is never analysed.
- **Decision wording.** H1c holds: "with Claude writing, dropping the default POV mode lowers windowed person mismatch by
  d (n = N)". Not holding: "no effect of the POV fix detectable with Claude writing at n = N (not equivalence)". Combined
  with W21's H1 in the close record: the fix ships to the product if H1 holds for Flash-Lite, and W21-C says whether the
  paid writer needs it too. Every line names: Claude-written, Sonnet-judged where a judge measure, not human, development
  corpora.
- **Conduct.** Only counts (chapters written, failures, cost, judge coverage) are read before the analysis.

## Ledger
Rows are kept in SPEC-95's ledger, labelled W21C.
