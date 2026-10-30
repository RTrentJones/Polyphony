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
  beside it. Below the bar, M3's descriptive line is marked unvalidated (H3 is descriptive in any case; see the
  pre-registration).
- **M2** carries E2's validation; it is re-checked here on the source chapters (accuracy reported, no bar).

## M1 validation (2026-10-30, script only, no LLM; before any arm data)
On the 17 source chapters (Dracula, Frankenstein, Wuthering Heights; all profile as first person, past tense, double
quotes):

| measure | false positives (bar ≤ 0.05) | detection (bar ≥ 0.90) | status |
|---|---|---|---|
| `person_mismatch` (per sentence) | mean 0.145, max 0.33 | 17/17 | fails: first-person narrators tell others' actions ("He turned…") |
| `person_mismatch_window` (runs of 5 classifiable sentences; a run is first person if any sentence is) | 0.00 on every chapter | 17/17 | **validated** |
| `tense_mismatch` | mean 0.25, max 1.0 | 15/17 | fails: Frankenstein's letters and Dracula ch. V are genuinely present tense |

M4 on the outlines: Dracula 0/12 character-less beats; Frankenstein 2/14 (chapter 3, which also has no POV); Wuthering
Heights 0/12. Both "invented" names the product's check flags are sentence-initial capitalised words (false positives).

## Principal review 1 (2026-10-30): DO NOT SIGN; changes adopted below
The arm switches were confirmed correct (default byte-identical, fingerprint unchanged, no cross-arm cache or checkpoint
reuse, app_sha and base_url refusals). Nine blocking items concerned the design and the statistic; each is answered in
the sections below: a balanced book × treatment × order table (1, 2); every arm pinned to one app sha and E4's runs never
arm A (3); a sign-flip cluster test in place of the percentile bootstrap, whose null rejection rate the review measured at
0.08–0.135 for nominal 0.05 at these cluster counts (4); the disclosure below (5); an instrument floor (6); H2 reworded with
its confound measured (7); H3 descriptive from the start (8); a budget contingency and a fixed stop (9). The review also
calibrated M1 on partial drift (source paragraphs rewritten to third person): half a chapter in third person scores
0.44–0.58 on `person_mismatch_window`; third person with every fourth paragraph left in first person scores about 0.5
(against about 0.8 per sentence). The window measure is lenient but not blind.

**Disclosure.** During engineering, M1 (including `person_mismatch_window`) was computed by script on the 12 chapters of
E4 run 40837155387 (wave 18, whose prompts are byte-identical to arm A) before H1's measure was amended. The coordinator
did not open those values. The amendment rests on the source-text validation alone (sentence-level false positives 0.145
against a 0.05 bar), and sentence-level `person_mismatch` is kept as a pre-registered sensitivity. SPEC-95 records the
same deviation for E4: E4's chapters were scored outside its pre-registration; its primary is untouched.

## Design
- **Books and outlines.** Dracula (outline run 40651541095, digest 9ff4f200…) and Frankenstein (40651268575, 5b417a71…;
  its chapter 3 fails before prose on every arm alike and drops out as `no_scenes`) from E4, and Wuthering Heights
  (outline-only run 41125885372, 2026-10-30, app 9310e578, 6 chapters of 2 beats, every beat with a character, digest
  `0b15bd10cbe29317fdea0b49fabac24aaad9304832f046d135c0474e47eecde9`, 13 generator requests). Each run writes the
  outline's chapters with E4's `chapters` step (`replicates=1`) on the same fixed outline, so arms differ only in the
  switch.
- **App.** Every arm, A included, dispatches with `app_sha` = the signed harness sha (main's merge of this wave), so A
  and the treatments run identical code. E4's runs (W17 or W18) are never arm A; the analysis refuses any report whose
  `app_sha` differs or that carries no explicit `ablation` key.
- **Day table.** One table day per Pacific day, after E4's book-day; day 1 is the first Pacific day after the signature.
  Books rotate D(racula), W(uthering), F(rankenstein); treatment pairs rotate so each pair meets each book once in nine
  days, and A runs first, second and third equally often:

  | day | book | runs, in dispatch order |
  |---|---|---|
  | 1 | D | A, P, N |
  | 2 | W | N, A, V |
  | 3 | F | V, P, A |
  | 4 | D | A, V, N |
  | 5 | W | P, A, V |
  | 6 | F | N, P, A |
  | 7 | D | A, P, V |
  | 8 | W | N, A, P |
  | 9 | F | V, N, A |

  Each treatment meets each book twice in nine days. The table repeats from day 1. After E4's last book-day (Nov 10), up
  to two table days run per Pacific day, in table order.
- **Dispatch.** Generation runs are `claude=no` and share one concurrency group that cancels a run in progress, so the
  next run is dispatched only after the previous one has completed. Judge runs (`steps=narration_judges`,
  `claude=yes judge=claude`, `claude_usd_cap` each) follow once the day's three runs exist, one at a time.
- **Pairing.** By the ledger's day label ("W21 day d", naming its runs), never by clock date. A treatment chapter pairs
  with A's chapter of the same day, book and outline position.
- **Budget contingency.** Before a day's runs, the remaining generator budget is read (≤ 400 a Pacific day including E4's
  ≈ 230). If it cannot cover three runs (≈ 55 each) plus a 40-request reserve, the day runs A plus the first treatment of
  its row; the skipped arm is recorded and the next day that has room runs it with a new A on the same book. A is never
  dropped.
- **Stop.** Generation ends at the end of Nov 12 Pacific, whatever n has been reached; judging finishes by Nov 13; the
  analysis runs once, before Nov 14 (the Claude credit's end). The stop is decided by date and counts, never by outcomes.
- **Unit and n.** A paired chapter. Each nine-day cycle gives each treatment 6 days × ≈ 6 chapters ≈ 34 pairs over 17
  clusters (book, outline position).
- **Cost.** Generation $0. M2 and M3 by Sonnet ≈ $0.04 a chapter, ≈ 200 chapters ≈ $8, plus validation ≈ $1. The wave's
  cap is $15 of the $64.

## Pre-registration (to be signed by the principal before any arm data is generated)
- **Hypotheses and primary measures.** H1: P changes `person_mismatch_window` against A, with the direction required to be
  lower (amended 2026-10-30 after the M1 validation above, before any arm data; see the disclosure). H2: N changes M2
  (narrator attribution) against A, with the direction required to be lower. Under the default mode the excerpts are the
  book's narration up to the scene's position, not necessarily the planned narrator's own, and they are the prompt's
  only first-person cue; so H2 is a test of attribution, not of "the excerpts carry the narrator's voice". H3 (V against
  A on M3) is descriptive only: M3 yields about one eligible line a chapter on generated text, too few for a test.
- **Statistic.** Per contrast, the mean paired difference (treatment − A) per cluster (book, outline position), then an
  exact sign-flip randomization test on the cluster means (all 2^k sign patterns), two-sided, with the direction
  required for a holding result; a t-interval over cluster means (df = k − 1) is the reported interval, and the
  percentile bootstrap is reported as a descriptive line. Holm's correction over the family {H1, H2} at family-wise
  0.05; a primary whose measure failed validation leaves the family before arm data. Judge measures use Sonnet; Groq is
  reported beside it and never changes a decision.
- **Instrument floor (read at analysis).** If A's M2 t-interval includes 0.5, H2 reads "undetermined: the instrument is
  at its floor on generated text", and no removal wording follows. The same holds for M3's descriptive line when A's M3
  is not above chance + 0.20.
- **Sensitivities (descriptive, pre-registered).** H1 with sentence-level `person_mismatch` (its direction should agree);
  N against A on `person_mismatch_window` (whether N moves person); H2 restricted to pairs where both chapters are first
  person by M1; M3 without narrator-credited ("I said") lines; per-arm product failures (`no_scenes`, failure classes)
  as an outcome; per-arm 8-gram overlap between a chapter and M2's voice samples.
- **Exclusions.** A pair is left out when either side's chapter has a quota failure or no scenes; counted per arm.
- **Decision wording.** Exploratory; no automatic product change. H1 holds (lower after Holm): "dropping the default POV
  mode lowers windowed person mismatch by d", and the fix is proposed for the next product wave. H2 holds (lower):
  "without the book-narration excerpts, the judge attributes the narration to the planned narrator less often by d".
  H2 not holding: "no effect of the excerpts on narrator attribution detectable at n = N (not equivalence)", and the
  lever becomes a candidate for removal, decided with E4's result, unless the instrument floor applies. H2 with d > 0
  (N attributes better): "the excerpts mislead attribution", and the excerpt selection is revisited in the next product
  wave. Every line names: Flash-Lite-written, Sonnet-judged where it is a judge measure, not human, development corpora.
- **Conduct.** The analysis runs once, after the stop, reading every run named in the ledger's day labels. Outcomes are
  not read before then; only counts (chapters written, failures, requests, judge coverage) are, to steer the schedule.

## Ledger
Rows are kept in SPEC-95's ledger, labelled W21.
