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
- **Stop.** The earliest of: the next unit would take W21-C past its $32 cap (≈ $4.7 a unit); the next unit would take
  the whole ledger past $58 ($64 less a $6 reserve), with W21's signed work taking priority; or the end of Nov 16 Pacific.
  Judging finishes by Nov 18; the analysis runs once, with W21's, by Nov 19. Decided by date and spend, never by outcomes.
  At $4.7 a unit the cap allows six units (two per book: 17 clusters, about 34 pairs). Each generation dispatch passes the
  same `claude_usd_cap` for A and P ($3.00) and the judge dispatch its own ($1.00); a quota or cap failure on either side
  excludes the pair, counted. Each dispatch on `main` follows SPEC-96's dispatch-ref check (`git diff ebed0aa origin/main --
  app alembic alembic.ini requirements.txt evals .github/workflows/evals.yml` empty).
- **Budget (D20-1's ledger).** Spent so far $15.81 (including the pilot). W21's remaining judging ≈ $8, E4's last three
  compares ≈ $1.7, W21-C ≤ $32, reserve ≈ $6: total ≤ $64.

## Pre-registration (signed by the principal before any W21-C data)
- **Hypothesis.** H1c: with Claude writing, P changes `person_mismatch_window` against A, with the direction required to
  be lower. One primary test; no multiplicity correction.
- **Statistic.** SPEC-96's: per-cluster (book, outline position) mean paired difference (P − A), exact two-sided
  sign-flip test on the cluster means, t-interval reported, percentile bootstrap descriptive.
- **Analysis procedure.** Before the analysis, every W21-C report's `generator` is checked to be the same Claude model and
  its `ablation` to be blank or `pov_fix`, and every W21 report's `generator` to be Flash-Lite; the result is recorded in
  the ledger, and a mismatch stops the analysis for review. The analysis runs `python -m evals.narration.analyze run …
  --app-sha ebed0aa1a0c5b1e28da30b927ac294f88aa70cd8 --days <W21-C-only file>`. W21-C reads only the P line's `d`, raw
  sign-flip `p`, `t_ci` and `n_clusters`; the tool's Holm values, decision lines and caveat belong to SPEC-96 and are
  disregarded. The decision is taken in this order: (i) `n_clusters` < 7: "undetermined: too few clusters"; (ii) no
  room: A's per-cluster means of `person_mismatch_window` over the paired chapters, with `analyze.t_interval` (df = k − 1),
  have an upper bound below 0.05: "no room: the Claude writer already holds the book's person under A" (d and p reported,
  descriptive); (iii) otherwise H1c holds when p ≤ 0.05 and d < 0. A's mean `person_mismatch_window` is reported beside
  the room verdict.
- **Descriptive.** M2 (narrator attribution, Sonnet and Groq), M3 (speaker attribution), sentence-level person and
  tense mismatch, per-arm failures, and the same measures for W21's Flash-Lite A and P beside them (unpaired across
  writers, descriptive only).
- **Exclusions.** As SPEC-96. The cost pilot (run 41247908629) is never analysed.
- **Decision wording.** H1c holds: "with Claude writing, dropping the default POV mode lowers windowed person mismatch by
  d (n = N)". Reversed (p ≤ 0.05 and d > 0): "with Claude writing, the POV fix raises windowed person mismatch by d (n =
  N)", and the fix does not ship for the Claude writer path without review, even if H1 holds for Flash-Lite. Not holding:
  "no effect of the POV fix detectable with Claude writing at n = N (not equivalence)", followed by the smallest effect
  with power ≥ 0.80 from `analyze.power(k, effect, sd = the observed sd of the cluster-mean differences, reps = 1,
  alpha = 0.05, sims = 400, seed = 0)` (reps = 1 because that sd is already of cluster means; corrected before any data
  after a Codex review of PR #81) on a 0.01 grid. Combined with W21's H1 in the close record: the
  fix ships to the product if H1 holds for Flash-Lite, and W21-C says whether the paid writer needs it too. Every line
  names: Claude-written, Claude-written and Sonnet-judged (same family) where a judge measure, not human, development
  corpora. Pair counts are reported per book and per first arm, so an imbalance at an early stop shows.
- **Conduct.** Only counts (chapters written, failures, cost, judge coverage) are read before the analysis.

Signed: principal, 2026-10-31. SPEC-97 (W21-C) pre-registration as written above, with B1–B3 of principal review 1 (analysis procedure and order of the cluster, room and test rules; reversed and null wording with the smallest detectable effect; ledger-wide and per-run caps), harness at cc34b82 merged as ebed0aa (ebed0aa1a0c5b1e28da30b927ac294f88aa70cd8), no harness change. Arms A and P only, `claude=yes claude_generate=yes judge=claude`, `app_sha=ebed0aa1a0c5b1e28da30b927ac294f88aa70cd8`, dispatched on `main` after SPEC-96's dispatch-ref check, one unit a Pacific day, books D, W, F, first arm alternating from A, paired by the ledger label 'W21C day d'. One primary test, H1c: exact two-sided sign-flip on cluster means of `person_mismatch_window` (P − A), holding at p ≤ 0.05 with d < 0; no multiplicity correction; fewer than 7 clusters is undetermined; the no-room rule precedes the test. The analysis reads only the P line's raw statistics from `evals.narration.analyze` with a W21-C-only `--days` file, after the `generator` check; the tool's Holm values and decision lines are disregarded. The cost pilot (run 41247908629) is never analysed. Only counts and cost are read before the analysis.

## Ledger
Rows are kept in SPEC-95's ledger, labelled W21C.
