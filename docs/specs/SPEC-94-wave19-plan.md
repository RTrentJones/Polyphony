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
| S7 | **Validate RN and RC before trusting them.** A small blinded set of intact source chapters and deliberately damaged versions of each (narrator drift, a duplicated event, broken transitions, the planned change removed), built deterministically; the check is whether RN and RC reliably score the damaged version below the intact one, damage by damage. A sample of the same items goes into S4's human set. |
| S8 | **Evidence beside every mean.** RN reports its eligible and scored scene counts, RC its scored-repetition count and how many repetitions were trimmed, so a mean never stands without the evidence under it. |

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

  **Amended 2026-10-26 (the user): "Budget is 100 a month total."** The monthly eval total is $100 (`evals.yml`'s default
  `claude_monthly_usd`; the `EVAL_CLAUDE_MONTHLY_USD` repo variable, if set, still wins and must say 100). October has about
  $64 left after ≈ $35.51 spent, so S5's holdout run, the Wuthering re-run of S7 and W18-4's after-draws need not wait for
  November.
- **D19-7 S4 as built** (`evals/calibration/`, `calibration.yml`). The inputs live only in Actions artifacts: R2's in
  `eval-report-*` (90 days), the pairs in W12-9's `baseline-bundle-*`/`baseline-ratings` (30 days, so the export must be
  dispatched before ≈11-18, after S1's last rating). R2's judge returns a score, not per-line attributions, so human-vs-judge
  R2 is compared by R2's own bands (the human's per-scene accuracy mapped to 1.0/0.7/0.4/0.0 against the judge's score), and
  per-line judge accuracy is an optional November item. Where a scene's speakers came from a context judge (`r2_source`
  judge/mixed) the "true speaker" is itself a reading, so tag-sourced scenes come first and are reported separately. The key
  carries arm labels only with `with_arms: yes`, which waits until S1 is published.

- **D19-8 S2 rules as built.** A scene, outline or revision is `None` (left out of the step mean, counted in `unscored.items`)
  when any rubric the judge did not score is None: unparseable, a repeated "nothing was provided", or the chain unavailable. A
  rubric that does not apply (R2 with fewer than two voiced speakers, RN without narration) is still left out of the mean, as
  before. It is never averaged over whichever rubrics parsed, since R2 runs far below R3–R5; claimed-empty scenes therefore
  move from a partial mean to None. A JSON reply without a numeric score is unparseable too, which old records cannot show, so
  `rescore` covers only the "unparseable judge reply" reason. Continuity matches a finding by its quote overlapping the
  injection's changed words (a quote under 8 characters must cover them), or by its detail naming them when they occur once in
  the text; recall may read lower than in earlier draws for that reason, and the change is the point. Injections outside the
  graded window are reported, not skipped silently (wuthering's `name-misspell` had been).
  **Amended after the principal review of the integrated wave (S2 as fixed):** (1) *either side of the
  contradiction is credited* — a finding matches when its quote overlaps the changed words **or** its
  detail names them where they occur once, whatever it quotes, since a correct finding may quote the
  other, untouched side; (2) *typography is normalised* on both sides before a quote is located or a
  name counted (curly quotes and apostrophes to straight, any dash or `--` to one dash, no space around
  a dash or before closing punctuation, case folded), and a quote that locates nowhere falls back to the
  detail rule; (3) *a deterministic zero wins over unscored* — R1 at 0 (capped or judged) makes the scene
  0.0, and an outline that failed its structural check is 0.0, even when another rubric went unscored;
  such an item is scored, not counted in `unscored.items`. An injection whose anchor overlaps an earlier
  injection, or whose span a longer replacement pushes past the 12,000-character window, is reported
  unapplied with that reason. `rescore` recomputes every item with the live rule, so claimed-empty
  scenes in old records also move to None; on a `--repeat` step its tally is marked "last pass only".
  Over repeats the book block's `unscored` tally sums every pass, scored or not (a pass whose every scene went unscored
  still carries its RC/RN judgments), while its means and their evidence (`rn_*_total`, `rc_*_passes`) stay over the
  scored passes.

- **D19-9 No new metrics this wave** (a second outside review, 2026-10-25, on `5bb2df2`): RN and RC are new and unvalidated,
  so wave 19 adds none and validates these two (S7) instead. RC on a chapter over ≈3,000 words reads the scenes with their
  middles trimmed, so a trimmed RC is a partial-chapter assessment and is reported as such; repetition or contradiction inside
  a trimmed middle is invisible to it. S7's damage is placed outside the trimmed region and, separately, inside it, so the
  blind spot is measured rather than assumed.

- **D19-10 S5/S3 as built.** The holdout corpora are `holdout-a` (*The Woman in White*) and `holdout-b` (*The Moonstone*):
  opaque names, because an eval book's title reached the canon prompt as `eval-<corpus>`, so the development corpora's titles
  named their novels. Every eval title becomes opaque in this wave; that removes a recall cue from development measurements, so
  draws before and after it are not strictly comparable. Every corpus declares `"split"`; the corpus readers refuse a holdout
  book unless `evals.run --holdout` names it, and no default, workflow or history names one. The stronger renaming check found
  originals left in the development corpora (Dracula, Wuthering), fixed in the same change. The holdout's synopsis, beats and
  chapter attributions are checked against the full source before the freeze; `holdout-v1` tags the merge commit that carries
  the corpora and `evals/tools/rename.py`.
  Titles are `eval-<step>-<8 hex of the corpus id's sha256>`. The first draws after this change carry the experiment label
  `opaque-titles` so Tracer keeps them apart. holdout-a's identifying phrase (the woman's dress) is renamed like the
  Moonstone's gem; its colour injection moves with it.
  `evals/baseline/` keeps its `eval-baseline-<corpus>` title: it stays frozen at W12-9's P⁵ until that result is published.
  The title reached every arm alike, so it is a recall cue shared by all arms, not a bias between them, and the write-up
  says so.

- **D19-11 S7 as built** (`evals/validation/`, the opt-in `rnrc_validation` step). Six development
  chapters (`chapters.json`: Frankenstein's Letter 4 and Chapter 5, Dracula's Chapter II, Wuthering's
  Chapters I, III and IX); three fit RC's budget and are seen whole, three are trimmed in the middle.
  Each is damaged deterministically four ways — narrator drift, a duplicated event, reordered scenes
  (broken transitions), the planned change removed — and on a trimmed chapter the damage is placed
  outside, inside and partly inside RC's trimmed region, so D19-9's blind spot is measured: 36 items,
  72 judge calls per run (RC on each item, RN on one scene of each), free on the free judges and
  ≈$0.61 if every call fell through to Sonnet (≈$1.20 worst case). A sample of intact/damaged pairs
  goes into S4's page as `chapter` items, which the human answers on RC's own anchors (0 / 0.4 / 0.7 /
  1.0). The source chapters are famous and the judges have likely read them, so a result is an
  **upper bound** on RC's (and RN's) sensitivity until it is repeated on generated chapters or on the
  holdout. The holdout itself runs only from a tag: `evals.yml` refuses `holdout=yes` dispatched
  from a branch.
  Review rules: the step refuses `--repeat` > 1 (its null score would make the repeat fold keep only
  the last pass) — repeats are separate dispatches, averaged per item by `analyze`, which reports the
  judgments each mean rests on; `agreement` scores `chapter` items (human RC vs `judge_rc`: mean
  absolute difference, same-anchor share, damaged-below-intact ordering for both and their accord);
  every `analyze` CI resamples chapters as clusters, a chapter's variants averaged first so each
  chapter weighs once (n_chapters beside n_observations). The export with `--reports` rebuilds the
  item set from the reports' seed, keeps their digest in the key, and refuses (as `analyze` does) a
  set whose digest differs, since `judge_rc` joins by item id and an id survives a changed chapter.

## Sequence
1. 10-26 00:07Z — W12-9 day 6 (S1). 05:00Z — W17 draw 2; then the wave-18 product PR.
2. In parallel now, on branches: S2 (code + tests), S3 (docs), S5 (corpus build), S4's export (items + key, no LLM where
   existing records suffice). None merges before step 1's draw.
3. After S2 merges: the S4 page goes up; the user rates; the agreement script runs.
4. S1 result + S4 agreement + S5 holdout run (November) → S6 case study, then this spec's close record.
