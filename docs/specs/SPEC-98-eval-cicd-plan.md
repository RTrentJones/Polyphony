# SPEC-98 — Evals in CI/CD: a measured hill climb
**Depends on**: SPEC-94 (holdout), SPEC-95 (E0 judges, E4 `app_sha` and compare, the ledger), SPEC-96 (script measure M1,
objective attribution items, the sign-flip test), SPEC-81 (spend enforcement). **Directed** by the user on 2026-11-15:
"Create a plan to execute all of this. The end result should be CI/CD measured evals to ensure we are hill climbing.
Include where we should use Anthropic's API (where I have $100 credit a month from my max plan) vs free tiers".
**Status** (2026-11-19): P0 is done; wave 21's record (6013e43, PR #86) is on main, and with it the castless-chapter
repair under `app/` (578b0ad, PR #87, D98-5) and the experiment tag guard (7186a01, PR #88, D98-1). P1 is in progress
(each piece is marked **Built** below), and P2's set builder (d5e6899) is on the working branch: it reads only wave
21's recorded chapters, so it was built before P1's acceptance check, while P2's judge runs wait for P1.

## Why
Wave 18's prose changes shipped green and were found two weeks later to be preferred to wave 17's about 3 times in 10
(SPEC-95 E4). Wave 21 then needed 24 days to ask which lever did it. Four causes, each from the record:

1. No check compares a candidate with the last release before it deploys. PR CI gates plumbing (stub LLM, `cel` cast
   fidelity); `greenlight-build.yml` deploys every push to main.
2. The judges have no human anchor. The free chain is position-driven (order-consistent on 1/66 pairs); Sonnet's retest
   on near-ties is weak (κ 0.32); the calibration set (SPEC-95 E5) was never rated by a person.
3. Experiment identity is tied to main, so main freezes for the length of an experiment, and the runner, ledger and run
   lists live in an agent session's scratch directory, which a container restart has now killed three times.
4. Reports live in workflow artifacts that expire, the holdout has never run (`holdout-v1` was never tagged), and there
   are no release tags to hang a trend on.

## The end state
Every commit that reaches production has passed a paired comparison with the release before it, under a rule signed
once. Every release adds one row to a scoreboard: the gate's statistic, the script measures and the chapter failure
rate, and, for the releases the release line pays for (at most two a month, below), the holdout; a row without one
says so. The hill climb is that scoreboard, read release over release, with the holdout as the headline and the
development numbers beside it.

## Which provider does what
The split follows one rule: **the free tiers write, Anthropic judges.** The writer under test must be the product's
default writer (BRD free-first tiering), and its quota is the throughput limit: Flash-Lite's measured 500 requests a
Pacific day (BRD R7.8), of which the harness plans at most 400 (SPEC-95's ⌊0.8 × 500⌋), about 55 a six-chapter run. Money cannot raise that limit; it buys judgment, which is where the free tiers have failed.

| Work | Provider | Why |
|---|---|---|
| Chapter and outline generation, every tier | Gemini free tier (the product default) | It is the product; free |
| Unit, stub end-to-end, `cel` fidelity, script measures (M1, narration lint, failure rate) | none (local, stub) | Deterministic; runs on every PR |
| Retrieval and attribution embeddings | fastembed (local) | Free, deterministic |
| Smoke judging on PRs, if a free judge qualifies | Groq and the other free judges | Never decides; must pass D98-4's qualification first |
| The candidate gate's judge | Anthropic, Sonnet, if it qualifies in P2 | The most order-consistent judge on record (same answer in both orders on 39/66 E4 pairs, against the free chain's 1/66), though still below P2's bar; if no judge qualifies, the gate runs on its guard measures alone (P2) |
| Bulk or first-pass judging, if it qualifies | Anthropic, Haiku | About a twentieth of Sonnet's price; qualifies on the calibration set or is not used |
| Strict re-judge of a decision, calibration audits | Anthropic, Opus | Second opinion on results that change the product (SPEC-96 Amendment 2) |
| The Claude-written product tier (BRD R7.9) | Anthropic, as the writer | Once per release, small; about $2.25 per six chapters (SPEC-97's pilot) |
| The holdout's judging | Anthropic, Sonnet | Once per release |

Two cost levers apply to all Anthropic judging and are measured before they are relied on: the Message Batches API (half
price; judging is not latency-sensitive) and prompt caching of the rubric and chapter prefix.

### Monthly budget ($100 Anthropic credit)
| Line | Cap | Basis |
|---|---|---|
| Candidate gate | $30 | at most $1 a run (wave 21's Sonnet judging ran $0.18–0.27 per 15–18 chapters; a 12-pair two-order compare is estimated at $0.40–0.60, to be measured in P3) |
| Experiments | $35 | one pre-registered experiment a month at wave 21's scale |
| Release: holdout, Claude-written tier, strict re-judge | $20 | at most two releases a month: the first release of each half of the month; later releases in that half get no release-tier run |
| Calibration and judge qualification | $5 | |
| Reserve | $10 | never planned against |

`evals.yml` already subtracts the month's earlier spend from `EVAL_CLAUDE_MONTHLY_USD` (SPEC-90 D1). P1 adds a per-line
cap keyed by the run's `tier` input, so the gate cannot spend the experiment's money. **Built (2026-11-15):**
`evals.spend --tier <t> [--lines ...]` bounds a run's ceiling by what is left of its line and refuses a spent line; the
workflow records each run's tier in its artifact (`eval-tier.json`), and an attempt without one, every run before this,
counts as an experiment. The amounts are `evals/spend.py`'s `LINES`, replaced as a whole by the `EVAL_CLAUDE_LINES` repository
variable: a tier the variable leaves out has no line, and its runs are refused. Wave 21, which ran under the $64 ledger,
has already overspent November's experiment line (the other lines are unspent): the lines are planned from December,
and a November experiment-tier run needs `EVAL_CLAUDE_LINES` set for the month to all four tiers, with the experiment
amount above November's experiment spend; gate, release and calibration runs need no override.

## Decisions
- **D98-1 Experiments run from an immutable tag.** A harness is signed as a tag (`w22-harness`), dispatched with
  `--ref <tag>`, and the app under test is passed by `app_sha`. The runner's guard compares the tag, not main. Main is
  never frozen again.
  **Built (2026-11-19):** `evals/config.py` refuses an experiment-tier dispatch that names an `experiment` unless it
  comes from a tag (`GITHUB_REF_TYPE`); gate, release and calibration runs, and unnamed runs, are unaffected. The
  runner's own guard moves with it into the repo in P4.
- **D98-2 One standing gate rule, signed once.** Candidate against the last release on the frozen outlines, both orders,
  judged by a judge P2 qualifies to decide (Sonnet is the one expected; with none, the preference half of the rule
  stays advisory and only the guard measures block): the deploy is blocked when the interval for P(candidate > release) lies wholly below 0.5, or when a guard
  measure regresses (chapter failure rate, M1). The exact statistic, sample size and interval are fixed in P3 from wave
  21's unblinded variances, and the rule is validated by an A/A run (release against itself) before it enforces.
- **D98-3 Results are committed.** Each report is written to an orphan `eval-results` branch under
  `<tier>/<experiment or "untagged">/<run>[-<attempt>].json` (the first attempt has no suffix); analyzers read that
  branch by tier and experiment tag (`results.load(dest, tier, experiment)`). Tracer ingestion stays for trending.
  **Built (2026-11-15):** `evals/results.py` writes one bundle per run attempt (identity, the reports by file name,
  the ceiling and done markers) and `load()` reads an experiment's bundles back in order; `evals.yml`'s `publish` job
  commits it. That job holds the workflow's only write permission and none of the eval job's secrets; a push-triggered
  run, a run with no report, and a run that answered calibration items publish nothing.
- **D98-4 Judges qualify before they are paid or trusted.** A judge enters a tier only after an order-consistency test
  and a score against the human-rated calibration set; the thresholds are fixed in P2 before any judge is scored.
- **D98-5 Deterministic product failures are fixed when found.** With D98-1 a fix no longer disturbs a running
  experiment; Frankenstein's outline chapter 3 is the first.
  **Diagnosed (2026-11-15); repair built 2026-11-19 for newly generated outlines (user: "Follow your best
  recommendations"):** the frozen outline's chapter 3 ("Shadows and Letters") follows the
  creature, who is not in the renamed corpus's cast, so the outline saved it with no `pov` and no cast characters;
  `generate-planned` then refuses the whole chapter with 422 "Pick at least one character from the cast"
  (`app/characters/cast.py`). An author can pick a cast in the modal; the harness, like a one-click author, sends
  none. The chapter prompt allows `"pov": "canon character name or ''"`. A required cast `pov` was considered and not
  built, since it would change R1.5a. As built, the first chapter pass is unchanged (its prompt still allows `''`); the
  outline audit lists chapters that resolve to no cast member (`FidelityAudit.castless`), and that list triggers the one
  chapter-stage repair the principal gate already had, with those chapters as feedback. The repair is one best-effort
  pass, so a chapter it leaves without a cast is only a warning (BRD R1.4, R1.6) and still gets 422 when written. It
  does not touch saved outlines: the frozen Frankenstein outline's chapter 3 still has no `pov` and still gets 422 until
  P3 re-records the frozen outlines for the gate (wave 21's stays as recorded), and no run yet shows a re-recorded
  chapter writing.
- **D98-6 Power comes from clusters.** New development corpora and chapters are added before days; an experiment's plan
  states its cluster count and the smallest effect it can detect before it starts.

## Phases
Each phase is one PR (or a short series) through the usual gate, and ends on a named acceptance check.

- **P0 — Close wave 21 (Nov 18–19).** As SPEC-96 says: finish the strict judging, analyse once, record the result and
  the ledger. Decide the wave 18 levers on E4 and wave 21 together. *Done when* the record PR is merged.
  **Done (2026-11-19; record PR #86):** SPEC-96's H1 and H2 do not hold (P: d −0.11, Holm p 0.19; N: d +0.02, p 1.0, with M2 at
  a ceiling); SPEC-97's H1c holds (with Claude writing, P lowers windowed person mismatch by 0.86, p 1.5 × 10⁻⁵). Wave 21
  measured attribution and cannot say which of wave 18's levers carries E4's preference loss, so that decision moves to
  a direct preference compare per lever, the first use of P3's compare step. The ledger closed at $60.43 of $64.
- **P1 — Foundations (about 3 days).** Tag-pinned dispatch (D98-1) and the guard change; the `eval-results` branch and
  the report writer (D98-3); the push gate restored in the repo as one script (`scripts/gate.sh`: black, ruff, pytest);
  `evals.yml` split into a reusable workflow with thin callers (`eval-pr`, `eval-candidate`, `eval-experiment`,
  `eval-holdout`), its mode guards moved from shell into one validated config object with unit tests; a `tier` label
  and per-line budget caps; the Frankenstein chapter 3 fix (D98-5). **Built so far:** the gate script, the
  per-line caps, the results branch (first bundle: `experiment/p1-publish-check/43697392971.json`), the config
  object (`evals/config.py`: every input rule checked in the job's first step, all problems reported at once; the
  step guards remain until the split), the D98-1 tag guard, and the D98-5 repair for new outlines. *Done when* an experiment-tier run dispatched from
  a tag completes while main has moved, and its report is on `eval-results`.
- **P2 — The anchor (amended 2026-11-19).** The user does not know the source books well enough to rate attribution
  ("I don't know the books well enough to be a rater") and chose a half-hour preference set. So the anchor has two
  parts. (a) **Objective items, no rater:** narrator and speaker attribution on the source texts, scored against the text
  itself (as SPEC-96's validation run 41145514872 did), for correctness. (b) **A blind preference set, one reader:**
  `evals/calibration/prefs.py` (seed 20261119) draws 16 pairs of passages that open the same planned scene of the same
  frozen outline (whole paragraphs, 150–300 words a side), 5 A–N, 5 A–V and 3 A–P from wave 21 on the same ledger day,
  and 3 Claude-written A against Flash-Lite A on the same outline position, one pair per outline chapter, sides
  randomised, plus 2 earlier items repeated with the sides swapped to measure the reader's own consistency. The reader
  sees the chapter summary, the scene's title and the two passages, and answers A, B or no preference on a private page
  (only its owner reads or writes the answers). The key stays out of the page and the repository; the seed and the
  builder regenerate it. **Qualification rule, fixed before any judge answers these items (counting made exact on
  2026-11-19, before the reader or any judge had answered):** a judge answers each of the 16 distinct pairs in both
  orders; the 2 swapped repeats measure the reader only and are not put to judges. A judge's answer on a pair is its
  pick when both orders give the same side, "no preference" when both orders say so, and inconsistent otherwise; its
  order-consistency is the share of the 16 pairs that are not inconsistent. A pair is decisive when the reader picked a
  side and, if the pair is repeated, picked the same side again; a pair whose repeat contradicts the first answer
  leaves the decisive set and is reported. Agreement counts the decisive pairs on which the judge's answer is the
  reader's side; an inconsistent or "no preference" answer counts as disagreement. With n decisive pairs, a judge
  qualifies to decide the gate with order-consistency of at least 13/16 and agreement of at least ⌈0.70 n⌉ pairs, and
  is a candidate for bulk first-pass judging with the same order-consistency and agreement of ⌈0.70 n⌉ − 1 (at 13
  decisive pairs, 10 and 9). These bounds are coarse, so every judge's numbers are reported with their counts, and the
  reader's own repeat consistency is reported beside them. They are also likely to qualify no one: 13 of the 16 pairs
  compare Flash-Lite arms between which wave 21 found no measurable difference, so they are near-ties, where Sonnet's
  order-consistency (39/66 in E4) and retest (κ 0.32) are weakest and the reader's own picks may be close to chance.
  **If no judge qualifies,** no judge decides: the gate's preference statistic is reported, labelled "not human-
  anchored", and never blocks, the gate blocks on its guard measures alone (the fallback under Risks), and P2 is rerun
  on a larger set, weighted toward pairs expected to differ, once P6 adds corpora. The A–N and A–V answers are also a first human read on wave 18's excerpts and VOICES rule, descriptive only at
  5 pairs each. *Done when* the reader has rated the set and every judge has a recorded agreement and order-consistency.
  **Built (2026-11-21):** the rated set's digest is `9e4dd77c775b0aed3d9209dc825d573975ac30a7050a0c964ebc53edb58b0f93`
  (`prefs.digest`). `calibration-prefs.yml` (no secret) rebuilds the set from the runs the committed day files name,
  refuses any set with another digest, and uploads the judges' copy, each distinct pair as `<id>-ab` and `<id>-ba`
  offering A, B or none, as the `calibration-items` artifact the existing `calibration_answers` step reads, with the
  key in its own artifact; `prefs_guard` refuses an item that carries anything from the key. `prefs.score` computes
  the table above from the reader's answers and each judge's.
- **P3 — The candidate gate (about 4 days, then 2 weeks advisory).** Frozen outlines (the three recorded in SPEC-96);
  release chapters generated once per release and cached on `eval-results`, regenerated weekly to catch provider drift;
  the first baseline, since no release exists yet, is the commit in production when P3 starts, tagged by hand as
  `release-0` (P5 automates the tags after it); the candidate's chapters generated per run (about 110 requests for two
  books of six chapters). Of the 400 planned requests a Pacific day, the gate takes at most 220, two candidates; the
  weekly regeneration uses one of those slots on its day, a candidate past the day's slots waits for the next day, and
  experiments get the other 180 (P4); the compare step; the rule's pre-registration (D98-2) with an A/A validation; the gate as a
  required job between the image build and the deploy notification in `greenlight-build.yml`. Advisory for two weeks
  (reports, does not block), then enforcing. *Done when* an A/A run passes, a deliberately degraded prompt is blocked,
  and the measured cost per run is recorded.
- **P4 — Experiments in the repo (about 3 days).** `eval-experiment` on a cron, driven by a committed plan file (days,
  arms, seeds, stop date) with its state and ledger on `eval-results`; one Claude run at a time through the existing
  concurrency group; the daily request budget shared with the gate by a fixed split (gate first). *Done when* a
  two-day dry-run experiment completes with no agent session involved.
- **P5 — Releases and the holdout (about 2 days).** Tag `holdout-v1`; cut a release tag per production deploy that
  passes the gate; `eval-holdout` on the release tags the release line pays for (at most two a month); the scoreboard (one row per release, committed and rendered).
  *Done when* the first release has a scoreboard row with a holdout number.
- **P6 — Power (ongoing).** Two or three more development corpora, built like the existing ones, to raise the cluster
  count (D98-6); Batches and caching measured on the gate's judging. *Done when* the gate's detectable effect is stated
  from data, not estimated.

## Order and dependencies
P0 → P1 → P2 and P3 in parallel (P3's enforcing mode waits for P2's judge table; P3 sets its own first baseline,
`release-0`) → P4 → P5 → P6. P1 is the only phase
everything else needs. The user owns three things: the rating sitting in P2, the signature on D98-2's rule in P3, and
the switch from advisory to enforcing.

## Risks
- **The gate's noise.** A paired preference at about 12 pairs may be too noisy to block on. The A/A run and the advisory
  fortnight measure this; if it is, the gate's sample grows to what the quota allows, or the rule falls back to the
  guard measures alone until P6 adds clusters.
- **Free-tier quota changes.** The gate and experiments share one daily quota; a cut halves throughput. The plan file
  and the gate both read the quota from a repo variable.
- **Sonnet as the deciding judge without a human.** Until P2 lands, every gate result keeps the label "Sonnet-judged,
  not human", and the gate stays advisory.
- **Development-set fit.** The gate tunes toward three books. The holdout row per release is the check on that, and a
  holdout drop is recorded, never fixed for (SPEC-94).

## Out of scope
A second paid provider; a CI system other than GitHub Actions; changes to the BRD's tiering.
