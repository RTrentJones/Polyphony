# SPEC-98 — Evals in CI/CD: a measured hill climb
**Depends on**: SPEC-94 (holdout), SPEC-95 (E0 judges, E4 `app_sha` and compare, the ledger), SPEC-96 (script measure M1,
objective attribution items, the sign-flip test), SPEC-81 (spend enforcement). **Directed** by the user on 2026-11-15:
"Create a plan to execute all of this. The end result should be CI/CD measured evals to ensure we are hill climbing.
Include where we should use Anthropic's API (where I have $100 credit a month from my max plan) vs free tiers".
**Status**: P1 in progress on the working branch (each piece is marked **Built** below). Main keeps wave 21's signed
harness (ebed0aa) until its last judge run, so none of this merges before wave 21's analysis is recorded, and nothing
under `app/` changes before then.

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
once. Every release adds one row to a scoreboard: the gate's statistic, the script measures, the chapter failure rate,
and the holdout. The hill climb is that scoreboard, read release over release, with the holdout as the headline and the
development numbers beside it.

## Which provider does what
The split follows one rule: **the free tiers write, Anthropic judges.** The writer under test must be the product's
default writer (BRD free-first tiering), and its quota (about 335 requests a UTC day, about 55 a six-chapter run) is the
throughput limit. Money cannot raise that limit; it buys judgment, which is where the free tiers have failed.

| Work | Provider | Why |
|---|---|---|
| Chapter and outline generation, every tier | Gemini free tier (the product default) | It is the product; free |
| Unit, stub end-to-end, `cel` fidelity, script measures (M1, narration lint, failure rate) | none (local, stub) | Deterministic; runs on every PR |
| Retrieval and attribution embeddings | fastembed (local) | Free, deterministic |
| Smoke judging on PRs, if a free judge qualifies | Groq and the other free judges | Never decides; must pass D98-4's qualification first |
| The candidate gate's judge | Anthropic, Sonnet | The only judge so far that is order-consistent enough to decide |
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
| Release: holdout, Claude-written tier, strict re-judge | $20 | per release, at most two a month |
| Calibration and judge qualification | $5 | |
| Reserve | $10 | never planned against |

`evals.yml` already subtracts the month's earlier spend from `EVAL_CLAUDE_MONTHLY_USD` (SPEC-90 D1). P1 adds a per-line
cap keyed by the run's `tier` input, so the gate cannot spend the experiment's money. **Built (2026-11-15):**
`evals.spend --tier <t> [--lines ...]` bounds a run's ceiling by what is left of its line and refuses a spent line; the
workflow records each run's tier in its artifact (`eval-tier.json`), and an attempt without one, every run before this,
counts as an experiment. The amounts are `evals/spend.py`'s `LINES`, overridden by the `EVAL_CLAUDE_LINES` repository
variable. November's experiment line is already overspent by wave 21, which ran under the $64 ledger: the lines apply
from December, and a November run on this harness needs `EVAL_CLAUDE_LINES` set for the month.

## Decisions
- **D98-1 Experiments run from an immutable tag.** A harness is signed as a tag (`w22-harness`), dispatched with
  `--ref <tag>`, and the app under test is passed by `app_sha`. The runner's guard compares the tag, not main. Main is
  never frozen again.
- **D98-2 One standing gate rule, signed once.** Candidate against the last release on the frozen outlines, both orders,
  Sonnet: the deploy is blocked when the interval for P(candidate > release) lies wholly below 0.5, or when a guard
  measure regresses (chapter failure rate, M1). The exact statistic, sample size and interval are fixed in P3 from wave
  21's unblinded variances, and the rule is validated by an A/A run (release against itself) before it enforces.
- **D98-3 Results are committed.** Each report is written to an orphan `eval-results` branch under
  `<tier>/<experiment>/<run>.json`; analyzers read that branch by experiment tag. Tracer ingestion stays for trending.
  **Built (2026-11-15):** `evals/results.py` writes one bundle per run attempt (identity, the reports by file name,
  the ceiling and done markers) and `load()` reads an experiment's bundles back in order; `evals.yml`'s `publish` job
  commits it. That job holds the workflow's only write permission and none of the eval job's secrets; a push-triggered
  run, a run with no report, and a run that answered calibration items publish nothing.
- **D98-4 Judges qualify before they are paid or trusted.** A judge enters a tier only after an order-consistency test
  and a score against the human-rated calibration set; the thresholds are fixed in P2 before any judge is scored.
- **D98-5 Deterministic product failures are fixed when found.** With D98-1 a fix no longer disturbs a running
  experiment; Frankenstein's outline chapter 3 is the first.
  **Diagnosed (2026-11-15), not yet fixed:** the frozen outline's chapter 3 ("Shadows and Letters") follows the
  creature, who is not in the renamed corpus's cast, so the outline saved it with no `pov` and no cast characters;
  `generate-planned` then refuses the whole chapter with 422 "Pick at least one character from the cast"
  (`app/characters/cast.py`). An author can pick a cast in the modal; the harness, like a one-click author, sends
  none. The chapter prompt allows `"pov": "canon character name or ''"`. Proposed fix, for the user's decision since
  it changes R1.5a: the chapter stage requires a cast `pov` for every chapter, repairs a chapter that has none once
  (as it already does for omitted principals), and the outline audit warns on any that remains. Frozen outlines are
  re-recorded for the gate in P3; wave 21's stays as recorded.
- **D98-6 Power comes from clusters.** New development corpora and chapters are added before days; an experiment's plan
  states its cluster count and the smallest effect it can detect before it starts.

## Phases
Each phase is one PR (or a short series) through the usual gate, and ends on a named acceptance check.

- **P0 — Close wave 21 (Nov 18–19).** As SPEC-96 says: finish the strict judging, analyse once, record the result and
  the ledger. Decide the wave 18 levers on E4 and wave 21 together. *Done when* the record PR is merged.
- **P1 — Foundations (about 3 days).** Tag-pinned dispatch (D98-1) and the guard change; the `eval-results` branch and
  the report writer (D98-3); the push gate restored in the repo as one script (`scripts/gate.sh`: black, ruff, pytest);
  `evals.yml` split into a reusable workflow with thin callers (`eval-pr`, `eval-candidate`, `eval-experiment`,
  `eval-holdout`), its mode guards moved from shell into one validated config object with unit tests; a `tier` label
  and per-line budget caps; the Frankenstein chapter 3 fix (D98-5). **Built so far:** the gate script, the
  per-line caps, the results branch (first bundle: `experiment/p1-publish-check/43697392971.json`), and the config
  object (`evals/config.py`: every input rule checked in the job's first step, all problems reported at once; the
  step guards remain until the split). *Done when* an experiment-tier run dispatched from
  a tag completes while main has moved, and its report is on `eval-results`.
- **P2 — The anchor (about 2 days, plus about 2 hours of the user's rating time).** A calibration set of 60–100 items
  drawn from existing chapters (attribution items with a right answer, and preference pairs), rated once by the user
  through `calibration.yml`; qualification of Sonnet, Haiku, Opus and each free judge against it (D98-4). *Done when*
  every judge has a recorded accuracy and order-consistency, and each tier's judge is named from that table.
- **P3 — The candidate gate (about 4 days, then 2 weeks advisory).** Frozen outlines (the three recorded in SPEC-96);
  release chapters generated once per release and cached on `eval-results`, regenerated weekly to catch provider drift;
  the candidate's chapters generated per run (about 110 requests for two books of six chapters, so three candidates a
  day fit the quota); the compare step; the rule's pre-registration (D98-2) with an A/A validation; the gate as a
  required job between the image build and the deploy notification in `greenlight-build.yml`. Advisory for two weeks
  (reports, does not block), then enforcing. *Done when* an A/A run passes, a deliberately degraded prompt is blocked,
  and the measured cost per run is recorded.
- **P4 — Experiments in the repo (about 3 days).** `eval-experiment` on a cron, driven by a committed plan file (days,
  arms, seeds, stop date) with its state and ledger on `eval-results`; one Claude run at a time through the existing
  concurrency group; the daily request budget shared with the gate by a fixed split (gate first). *Done when* a
  two-day dry-run experiment completes with no agent session involved.
- **P5 — Releases and the holdout (about 2 days).** Tag `holdout-v1`; cut a release tag per production deploy that
  passes the gate; `eval-holdout` on the release tag; the scoreboard (one row per release, committed and rendered).
  *Done when* the first release has a scoreboard row with a holdout number.
- **P6 — Power (ongoing).** Two or three more development corpora, built like the existing ones, to raise the cluster
  count (D98-6); Batches and caching measured on the gate's judging. *Done when* the gate's detectable effect is stated
  from data, not estimated.

## Order and dependencies
P0 → P1 → P2 and P3 in parallel (P3's enforcing mode waits for P2's judge table) → P4 → P5 → P6. P1 is the only phase
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
