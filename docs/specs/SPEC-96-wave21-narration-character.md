# SPEC-96 — Wave-21: which narration and character-setup levers earn their place
**Depends on**: SPEC-93 (wave 18's levers W18-1 VOICES, W18-5 book narration), SPEC-95 (RN-v2 validated in E2; E4's
fixed-outline chapter harness; the $64 ledger), SPEC-94 (S7 set). **Directed** by the user on 2026-10-30: "Design and run
the experiment" (after "How can we design a better eval and experiment for narration and character setup?").

## Why
Wave 18 added two levers for voice: every beat names its speakers and ties each to their own block (W18-1, the VOICES
rule), and the book's own narration is the style reference (W18-5, narration excerpts). Neither was tested on its own: E4
compares wave 18 to wave 17 as a whole. A third candidate is a defect found in W18-4: when a book's style guide sets no
point of view (Dracula, Frankenstein and Wuthering Heights all set none), every prompt still states "Point of view: third
person limited" and then asks the writer to take person and tense from the excerpts. All three books are first-person.
The question is which of the three earns its place, measured with instruments that are validated before they report.

## Arms (one factor at a time against the shipped product)
Every arm is today's main app (identical to wave 18's, `7dbb8c24`), with one eval-only switch, `PROSE_ABLATION`
(default empty: product behaviour unchanged), set by a new `evals.yml` input `ablation` and recorded in each report.

| arm | `ablation` | what changes |
|---|---|---|
| A (control) | (empty) | wave 18 as shipped |
| P | `pov_fix` | a book whose style guide names no POV mode states no mode: the point-of-view line names the character only and says person and tense come from the book's narration |
| N | `no_excerpts` | no book-narration excerpts (W18-5 off); the style section falls back as before W18 |
| V | `no_voices` | no VOICES rule (W18-1 off) |

## Instruments
- **M1 narration contract (script, $0).** Over each scene's narration (text outside quotation marks, single or double):
  the first-person share of personal-pronoun subjects, the past-tense share of finite verbs (a lexical heuristic), and
  the quotation style. The book's expected values come from the same script on its source narration. Reported per
  chapter: `person_mismatch` (the share of narration sentences whose person disagrees with the book's), `tense_mismatch`,
  `quote_mismatch`.
- **M2 narrator attribution (judge).** RN-v2's forced choice (validated in E2: Sonnet 24/24, Groq 0.75 [0.58, 0.92]),
  applied to a generated chapter: samples of the chapter's planned narrator and of the book's drift donor, then a passage
  of the generated narration; correct = the planned narrator. Per chapter: the share of its passages attributed correctly.
- **M3 speaker attribution (judge, new).** A scene's quoted lines with speaker tags removed, the scene's cast listed in a
  seeded order; the judge names each line's speaker. Per chapter: the share named correctly among lines whose speaker the
  scene's own tags fix unambiguously (lines without a recoverable speaker are left out and counted).
- **M4 outline lint (script, $0, descriptive).** On outline runs: the share of planned beats with no character, POV
  characters outside the cast, invented names. Reported per book across repeated outline generations; no arm.

## Validation (before any arm data is read)
- **M1** on each book's source chapters (the corpus excerpts): `person_mismatch` and `tense_mismatch` ≤ 0.05 (false
  positives); on the same chapters with narration rewritten from first to third person and past to present by rule:
  detection ≥ 0.90. Below either bar, the failing measure is reported descriptively only.
- **M3** on source scenes with dialogue (ceiling): Sonnet accuracy ≥ chance + 0.20 across scenes, and Groq's reported
  beside it. Below the bar, M3 is descriptive only and H3 is not tested.
- **M2** carries E2's validation; it is re-checked here on the source chapters (accuracy reported, no bar).

## Design
- **Books and outlines.** Dracula (outline run 40651541095, digest 9ff4f200…) and Frankenstein (40651268575, 5b417a71…;
  its chapter 3 fails on every arm alike and is left out) from E4, and Wuthering Heights (one new outline-only run,
  digest recorded here before its first arm run). Each run writes the outline's chapters with E4's `chapters` step
  (`replicates=1`) on the same fixed outline, so arms differ only in the switch.
- **Days.** E4 keeps its morning book-day. Each later Pacific day runs A plus two treatments in a rotating order (day 1:
  A, P, N; day 2: A, N, V; day 3: A, V, P; …), one book per day in the order Dracula, Wuthering, Frankenstein, so every
  treatment meets A on the same day and book. Generation runs are `claude=no` and dispatched one at a time after E4's
  day; the judge runs follow (`claude=yes judge=claude`, each with `claude_usd_cap`). Total generator requests per day
  stay ≤ 400 with E4's included.
- **Unit and n.** A paired chapter: the same outline chapter written by A and by a treatment on the same day. Target ≥ 30
  pairs per contrast by Nov 12 (day rotation gives each treatment 2 of every 3 days; after E4 ends on Nov 10 the free
  quota allows more runs a day).
- **Cost.** Generation $0. M2 and M3 by Sonnet ≈ $0.04 a chapter, ≈ 200 chapters ≈ $8, plus validation ≈ $1. Each
  judge dispatch passes `claude_usd_cap`; the wave's cap is $15 of the $64.

## Pre-registration (to be signed by the principal before any arm data is generated)
- **Hypotheses and primary measures.** H1: P lowers `person_mismatch` against A. H2: N lowers M2 accuracy against A (the
  excerpts carry the narrator's voice). H3: V lowers M3 accuracy against A (the VOICES rule makes speakers
  recognisable).
- **Statistic.** For each contrast, the mean paired difference (treatment − A) over chapter pairs, with a 95% percentile
  bootstrap (10,000 resamples, seed 0) resampling outline chapters with all their pairs (clusters); Holm's correction
  across the three primary tests at family-wise 0.05, applied to bootstrap p-values (the share of resamples on the far
  side of 0, doubled). Judge measures use Sonnet; Groq's result is reported beside it and never changes a decision.
- **Exclusions.** A pair is left out when either side's chapter has a quota failure or no scenes; a chapter with no
  dialogue line with a recoverable speaker is left out of M3 only. Counts per arm are reported.
- **Decision wording.** Exploratory; no automatic product change. H1 holds (difference below 0 after Holm): "dropping the
  default POV mode lowers person mismatch by d" and the fix is proposed for the next product wave. H2 or H3 fails to
  hold: "no effect of the excerpts (VOICES rule) detectable at n = N (not equivalence)", and the lever becomes a
  candidate for removal, decided with E4's result. Every line names: Flash-Lite-written, Sonnet-judged where it is a
  judge measure, not human, development corpora.
- **Conduct.** The analysis runs once, after the last pair, reading every run named in the ledger. Outcomes are not read
  before then; only counts (chapters written, failures, requests, judge coverage) are, to steer the schedule.

## Ledger
Rows are kept in SPEC-95's ledger, labelled W21.
