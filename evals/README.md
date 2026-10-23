# Polyphony evals

Measure whether the creative pipeline actually works — extraction, voice, plot,
continuity, prose — against **renamed public-domain novels used as ground truth**.

## The idea

A finished novel is a labelled dataset: it gives the true cast, each character's
real dialogue (gold voice), the plot's shape, and a fixed timeline. Feeding a
model the *original* text would test its memory of a famous book, not the
pipeline — so every character and place is **renamed** (Mina Harker → Nora Vance,
Van Helsing → Verhoeven, …). The prose stays; the ground truth holds; memorization
is defeated. See `corpora/<book>/PROVENANCE.md` — all sources are US public domain
(Standard Ebooks CC0 / Project Gutenberg with the PG wrapper stripped). Only
renamed excerpts of a few chapters are committed, never whole novels.

## The steps

| step | what it measures | method | needs LLM |
|------|------------------|--------|-----------|
| `extraction`  | finds the right cast          | upload → extract → **F1** vs gold cast, plus principal bible depth and mention order | yes (extract) |
| `ingestion`   | upload yields usable voice     | upload → per-character indexed-chunk count → **fraction grounded** | yes (extract) |
| `retrieval`   | voices separate in embedding space | held-out line → nearest across pooled voices → **precision@k / MRR** | no (embedder) |
| `attribution` | generated line sounds like the character | test-dialogue → embed → nearest train-line centroid (`reference: "train"`, SPEC-74), less each centroid's mean similarity to the other characters' train lines (topic control, W15-4; `raw` is reported beside) → **top-1 accuracy** vs chance | yes (generate) |
| `outline`     | recovers the story's shape    | generate outline (premise confirmed with the author's edit) → **R1 × mean(R6, R3)** + unknown-name rates | yes |
| `continuity`  | catches contradictions        | inject known errors → **detection recall**, precision, quote-verbatim + false-positive rate | yes |
| `prose`       | end-to-end scene quality      | the product path: outline → promote → planned scenes → deterministic cast/seam checks + **R1 × mean(R2..R5)** per scene | yes |
| `revise`      | AI revision does what was asked, and only that | three revisions of one scene through the revise API → outside-range bytes + new-name checks + **mean(RA, R5)** per revision | yes |

`retrieval` (and the reference side of `attribution`) run in-process with the
app's own fastembed model — **free, no LLM, no server**. On the committed Dracula
corpus these score p@3 ≈ 0.82 / attribution ≈ 0.82 against a 0.33 chance baseline.

## Harness v2: an eval that tests the product

Since SPEC-22 the `cel` eval writes into a book an author would have: real cast entries
(role, description, goals, voice, aliases from `ground_truth.json`'s `characters`), the
`style.json` style guide (set on every eval book through `PUT /books/{id}/canon/style`), and a
voice-bearing excerpt. Corpora without `characters` keep the placeholder cast.

- **outline** confirms the premise checkpoint with the corpus' `premise_edit`, then is judged on
  R1 (cast fidelity), R6 (premise) and R3 (beats) against the rebuilt canon block:
  `score = R1 × mean(R6, R3)`, and a missing principal caps R1 at 0. It reports `unknown_rate`
  (over the fidelity grader's known names) and `unknown_rate_canon` / `unknown_canon` (over canon
  plus every synopsis proper noun).
- **prose** uploads the excerpt, commits the canon cast, stores the memoised outline into the
  book (`PUT /plans`), promotes it, and generates the planned scenes of the first chapter whose
  cast holds two or more principals, at the default budget. When that path is unavailable the
  step reports `path: "failed"` with the reason and a `None` score, and does not cache the
  failure (SPEC-39). It reports the batch's `queued` / `deferred` and `estimate_over_actual`
  (SPEC-34). Each scene gets deterministic checks first (cast recall caps R1; a beat-seam or
  scene-seam 6-gram overlap caps R4; invented names; the R1 rule is harness v3's, below), then R1–R5 from the judge for the first
  five scenes. R2 sees each speaker's gold lines and the scene's
  spoken lines with tags and names removed: since SPEC-90 (W15-3) only the lines the product's
  speech rule credits to a cast member with voice samples (kept per scene as `spoken_lines`), and
  R2 is `None` (not applicable, no judge call) under two such speakers. Each inner beat's description and prose come from
  `GET /api/v1/scenes/{id}` (`beats`): the harness reads the app through its API only.
- **continuity** on `cel` injects its five planted contradictions into the excerpt (a synopsis is
  not prose).
- **prompt contract** grades what the app actually sent the model, from the relay archive (below).
  It is not a registered step: `evals.run` adds `steps.prompt_contract` when `EVAL_RELAY_ARCHIVE`
  (an `answered/` dir) or `EVAL_RELAY_DIR` (its `answered/` subdir) is set, and grades only
  exchanges archived after the run started. The checks are `beats_block`, `prior_paragraphs`,
  `voice_dialogue` (samples cross-checked against `gold_lines` / `other_lines`), `beats_stage`,
  `outline_no_repr` and `style_first`, plus the wave-2 checks of harness v3 (below). Standalone:
  `python -m evals.graders.prompt_contract <answered-dir> --book cel`.

A judge answer that says the input was absent ("no dialogue was provided") is asked once more;
if it says so again the rubric is `None` with `judge_claimed_empty: true`, never 0 (W15-2).
Outline R3 gets its own body: the chapter summaries as THE PLAN, the scenes as THE TEXT TO CHECK.

**Comparability (SPEC-90 D6).** R2 and the outline score changed meaning with SPEC-90 PR A:
every row of `evals/history.csv` (W4b2–W11b) was graded by the old harness, whose R2 graded every
quoted line and scored a scene without dialogue 0.

The rubric texts and score arithmetic are in `graders/rubrics.py`, the n-gram seam check in
`graders/seams.py`. A deterministic failure caps its rubric at 0 whatever the judge says.
Reports carry `app_sha` (the app's `/__version`, else the harness checkout's `git rev-parse
HEAD`) and `harness_sha`.

## Harness v3: fair caps and contracts for wave 2

SPEC-28 fixed three ways the v2 eval misread the product: a binary, name-only R1 cap; a premise
fixture that contradicted the synopsis (`premise_edit.premise_restated` now ends "files it
correctly, and goes onward"); and no checks for wave 2's fixes. The cel cast's voice is stored as
`{"diction": …}`, so canon blocks no longer render "Voice: voice: …".

**The R1 cap (S4, wave 2.5).** A principal is on the page when a capitalised run of the scene
names them by `app.parsing.names.match` (honorifics and surnames count, so "Ms. Okafor said" is
Zara). R1 is capped at 0 only when the scene has a cast and none of it is named. A principal the
scene's own plan (brief and beat descriptions) names who is never named on the page is a warning,
"<name> is in the plan but never named", and the judge's R1 stands
(`steps/pipeline.py::_grade_scene_checks`). A bare-surname match can credit the wrong person in a
warning; that is acceptable for a warning. The prose cache key is `planned-v3`.

**Prompt-contract checks for wave 2** (`graders/prompt_contract.py`; each should be 100% of the
calls it checks):

| Check | Purposes | Passes when |
|---|---|---|
| `plan_cast_from_beat` | `plan_beats` | the "Available cast" line parses to someone, and equals the corpus cast among the outline beat's `people` (else the chapter's) plus the chapter POV; a prompt whose line parses but matches no beat is listed in `unmatched`, not failed |
| `no_blank_setting` | `plan_beats`, `beat_prose` | no empty `Setting:` line |
| `no_neutral_tone` | `plan_beats`, `beat_prose` | no `Emotional tone: neutral` (checked only when `style.json` has a tone), unless the matched outline beat chose "neutral" |
| `pov_line_has_mode_and_character` | `beat_prose`, `plan_beats`, `scene_revise` | `POV_LINE_RE` parses, `<who>` is the chapter POV (else the corpus `pov`), and in a one-head mode (`Pov.one_head`) "Narrate only what <who> … perceives or knows" follows |
| `secondary_has_context` | `beat_prose` | every name on the "Also named" line has a `### <name>` in `<secondary>` with an `Introduced:` line or voice samples |
| `story_position_line` | `beat_prose`, `plan_beats`, `scene_revise` (with a `<chapter>`) | exactly one "This scene is in chapter N of M." with 1 ≤ N ≤ M |
| `movement_names_in_scene` | `beat_prose` | no movement sample names a ground-truth person outside the prompt's cast; `movement_coverage` (principal blocks with a movement line) is reported and gated ≥ 0.8 in the eval |
| `no_duplicate_bible` | `beat_prose` | no bible lines under CHARACTERS IN THIS BEAT; each corpus character description appears exactly once |
| `no_doubled_voice_label` | every call | no `voice: voice:` |
| `reduce_evidence_depth` | `extraction_reduce` | each principal section has ≥ 3 evidence or quoted lines |

The grader imports every string it parses from `app/core/prompt_markers.py`, and
`tests/unit/test_prompt_roundtrip.py` grades the app's real prompts (built through its public entry
points, answered by the stub) at 1.0 on every check. `beat_prose_prompt_chars_median` and
`voice_dialogue.short_samples` (voice samples under three words) are reported, not gated. A check with no matching call reports
`n: 0, rate: None` and is left out of `score`, so an archive from before a contract existed still
grades. `beats_stage` also accepts the premise the run's own `outline:chapters` prompt confirmed,
so an archive from before the F8 fixture edit still grades.

**Deterministic step checks.**

- `extraction`: `principal_fields` ("k/3": description, goals and voice_characteristics all
  non-empty), `principal_fields_missing`, `principal_mentions`, and `principal_mentions_min`
  (the fewest name mentions of any principal, target ≥ 10; pronouns are not counted). Precision
  counts the corpus' `secondary_cast` as hits and lists them as `secondary_found`; recall stays
  over the principals (SPEC-33).
- `attribution`: per line `gap` (top-1 minus top-2 score) and `mean_gap`; `margin` is kept for
  misses. Every character gets the corpus' one symmetric `voice_situation`, k = 10 draws each,
  and every cast name form is masked to `⟨NAME⟩` in the generated and gold lines before
  embedding; per character a Wilson interval, and `w9_9_p0` when no lower bound beats 0.33
  (SPEC-67). Reported, never gated; the step reports each row's probe.
- `outline`: `outline_names_synopsis_secondaries` ("k/2", from the outline text and any
  `secondary_names`) and `outline_secondaries_named`. `extraction` also reports
  `canonical_names_ok` ("k/n" over the principals with a title alias, such as Edric's "Chief
  Executive Lich": proposed under their name, not the title).
- `prose`: per scene `planned_beat`, `cast_from_beat` (the scene cast is the beat's cast, else the
  chapter's, plus a secondary that now resolves, plus the chapter POV; `None` when no beat
  matches) and `deterministic_warnings`; per step `cast_from_beat` (a
  rate), `r1_capped_scenes` and `planned_beats`. The judge's scene content carries the planned
  beat, its cast, secondary people, setting, tone and POV character (empty fields omitted).

**The `revise` step.** It runs after `prose` and revises the prose step's first scene when that
scene lives in this app, else a scene created from its prose (or the excerpt) with no beats. Three
requests, each against the draft `GET /books/scenes/{id}/proposals` returns (the raw editable
text): "Tighten this paragraph" (paragraph 2), "Make Zara colder in this exchange" (her first
quoted paragraph and the next) and "Redo beat 2" (`beat_index: 1`, only when the scene has
beats). Each proposal is checked deterministically (`outside_identical`: the text outside the
range is byte-identical; `new_names`: capitalised names in the span that are not in the canon,
the synopsis or the draft), judged on **RA** (a new rubric: instruction adherence) and **R5**, then
discarded, so every revision is written against the same draft. Changed outside text caps RA at
0; a new name caps R5 at 0. Score = mean over revisions of mean(RA, R5). A request the API refuses
(for example a 409 for an edited or ambiguous beat) scores 0 with its `error`, and the others still
run.

## Running

```bash
# deterministic/embedding steps only — no server, no keys:
python -m evals.run --book dracula --steps retrieval

# full suite against a running Polyphony (local preview or a deployed URL):
export EVAL_BASE_URL=http://localhost:8000          # or https://polyphony.rtrentjones.dev
export EVAL_ADMIN_EMAIL=... EVAL_ADMIN_PASSWORD=...  # bootstrap admin (mints an invite)
export GEMINI_API_KEY=...                            # the model under test
python -m evals.run --book dracula --steps all --out report.json --export gl-export.json
```

The runner registers a **throwaway invite user** and does all work as them — it
never touches other users' data. Generations are cached under `EVAL_CACHE_DIR`
keyed by the app's `/__version` sha, so re-grading never re-generates.

## Trending in Tracer

Pass `--tracer` to POST the run to [Tracer](https://tracer.rtrentjones.dev)'s
`/api/ingest` as one `eval_run` (one case per scored step, `score` in `0..1`),
so voice/plot/continuity quality trends release-over-release instead of being
eyeballed:

```bash
export EVAL_TRACER_URL=https://tracer.rtrentjones.dev
export TRACER_INGEST_TOKEN=...        # bearer; unset → run still scores, just doesn't ingest
python -m evals.run --book dracula --steps all --tracer --out report.json
```

The mapping (`report.tracer_export`) trends the model **under test** (`EVAL_MODEL_LABEL`
/ `LLM_MODEL`, else the active provider's default model), keyed by the app's
`/__version` sha, so a prompt/architecture change shows as a step-score delta on
the dashboard. Each run carries:

- `suite` = the book, and `env` = `EVAL_ENV_LABEL` (where the run happened; CI:
  `ci`), so each book trends as its own series;
- one case per scored step (`category` `gated` or `reported`), with the step's
  numeric fields as case `metrics` and headline `<step>.<metric>` run metrics
  (e.g. `extraction.f1`, `retrieval.mrr`, `outline.cast_fidelity`) that Tracer
  charts per run. A repeated step (`--repeat N`) publishes only its averaged
  score and spread, since its other fields are the last draw's;
- `labels` for the effective judge (whether it self-graded, and whether it was
  unavailable or gave out partway), the harness sha, the declared run, and any
  skipped, unscored (ran but no score, e.g. a judge outage) or errored steps, so
  a missing step reads as "didn't run", not "fine". Tracer reads these: a run with
  a skipped, unscored or errored step is shown as *incomplete* and never counts
  as a regression, and a
  judge change against the previous run marks it *not comparable*;
- `labels.cadence = on-demand`, so a quiet week between dispatches doesn't read
  as stale on the dashboard; and `labels.experiment` when `EVAL_EXPERIMENT` is set;
- in CI, `repo` / `branch` / `source_url` (the Actions run), so the Tracer run
  page links back to the commit and the logs.

**CI:** `.github/workflows/evals.yml` boots a fresh app (pgvector service +
`GEMINI_API_KEY`), runs the suite, and ingests to Tracer. It is dispatched on
demand (before/after a change) or by pushing `evals/.run-eval`; there is no
schedule, because each run spends the shared Gemini free-tier quota. It needs
`GEMINI_API_KEY` and `TRACER_INGEST_TOKEN` as repo Actions secrets. Results:
<https://tracer.rtrentjones.dev/projects/polyphony>.

**Claude runs and the monthly budget (SPEC-90 D1).** `claude=yes` writes with
Claude (paid). Evals get their own monthly budget (`claude_monthly_usd`, else
the `EVAL_CLAUDE_MONTHLY_USD` repo variable, else $40). Each run boots an empty
database, so before boot the job sums this UTC month's spend from every run
attempt's `eval-report-<run>[-<attempt>]` artifact (`evals/spend.py`: per
attempt, the max `usage.month_cost_usd` over its boot-mode book reports; an
attempt killed before any usage counts at the ceiling it saved in
`eval-ceiling.json`; a missing or expired artifact counts 0) and sets the app's
`MONTHLY_COST_CEILING_USD` to what is left, or to `claude_usd_cap` when that is
tighter. Under $3 left (one book) it refuses to run. Claude runs share one
concurrency group and are never cancelled in progress, so two never overlap.

**The ingest token.** `TRACER_INGEST_TOKEN` must hold the same value as the
wrapper repo's `TF_VAR_TRACER_INGEST_TOKEN` (the bearer Tracer checks):

```bash
gh secret set TRACER_INGEST_TOKEN -R RTrentJones/Polyphony   # paste the value at the hidden prompt
```

To confirm it worked, dispatch a cheap run (e.g. `book=cel`, `steps=outline`)
and read the job summary: each book gets a `Tracer ingest` line, `ok — 201 {...}`
when it landed. Anything else (an unset token, a `401`, Tracer down) is
`NOT ingested` plus a `Tracer ingest` warning annotation on the run. The eval
itself never fails on an ingest problem, so check the summary rather than the
job's colour.

**`env_label` vs `experiment`.** `env_label` says *where* the run happened; keep
it `ci` for CI runs. An experiment name ("spec-merge", "judge-fix") goes in the
`experiment` input, which is stored as `labels.experiment`. Putting it in the env
would split every experiment into its own series, which then can't be compared
with the main line or with each other.

**Comparing models.** Dispatch the workflow twice on the same ref with the same
`book` and a different `model` input (blank = the provider's default; otherwise
it sets `LLM_MODEL`, which the app boots with and the run is labelled by). Then
open
<https://tracer.rtrentjones.dev/compare?tool=polyphony&mode=eval&suite=dracula&env=ci>
(swap `suite` for the book you ran). Each model is a column with its latest run,
and both columns show the same commit, so any difference is the model and not
the code.

**Reported steps.** A step whose status is `reported` (SPEC-60, e.g.
attribution) is sent as a case with `category: "reported"`. Tracer shows it as
"· reported": its score is trended, but it is left out of the pass rate, the
flaky/flip counts and the run's verdict.

**Legacy runs.** Runs from before the book became the suite used `env=ci-<book>`
(`ci-dracula`, `ci-frankenstein`). Tracer relabelled those to `env=ci`,
`suite=<book>` so they sit on the same series as new runs; the original value is
kept in `labels.legacy_env`. Older `ci-<experiment>-cel` envs were left as they
were.

## Judge

Rubric-scored steps (`outline`, `prose`) use a pluggable LLM judge. Grade with
a DIFFERENT family than the model under test — it removes self-preference bias
and takes judge calls off the shared Gemini daily budget. Free options in the
registry: `groq` (CI default), `cerebras`, `openrouter`, `mistral`; set
`EVAL_JUDGE_PROVIDER=<id>` + that provider's key env var. Fail-soft: if the
requested judge's key is unset the judge falls back to the app provider and the
report/Tracer record `judge.fell_back: true` + `judge.self: true` — so a
self-graded run is always labelled, never silent. Judge calls are temperature 0.
Note: switching judge families shifts absolute scores; compare trends within one
judge (the report's `judge.provider` records which one graded each run).

**A judge at its own endpoint.** `EVAL_JUDGE_BASE_URL` sends the judge to any
OpenAI-compatible URL, bypassing the provider chain: `EVAL_JUDGE_LABEL` names it in
the report (default `relay-judge`), `EVAL_JUDGE_MODEL` sets the model string, and
`EVAL_JUDGE_API_KEY` is optional (a relay takes any key). The report's `judge` block
records the `endpoint`, and `self` is false unless that URL equals
`EVAL_APP_LLM_BASE_URL`. The scorecard prints `@ <endpoint>`.

## Relay runs: an agent plays the model

`evals/relay_llm.py` is an OpenAI-compatible endpoint answered by whoever watches a
queue directory (in practice an agent), so live-quality generations and judgments
run without a vendor key or the free-tier quota.

```bash
RELAY_DIR=/tmp/relay-queue python -m evals.relay_llm serve --port 8299   # the app's model
RELAY_DIR=/tmp/judge-queue python -m evals.relay_llm serve --port 8298   # the judge
python -m evals.relay_llm pending                  # id, purpose, path of each waiting request
python -m evals.relay_llm answer <id> <file|->     # answer one (file, or stdin)
```

Queue layout under `RELAY_DIR` (default `./relay-queue`):

- `pending/<id>.json`: `{id, model, purpose, messages, max_tokens, response_format,
  created}`, written when a request arrives. The request blocks until it is answered.
- `done/<id>.json`: `{"content": "..."}`, written by the answerer (`answer` writes it
  atomically). The relay returns it and moves the request, with its `answer`, to
  `answered/<id>.json`. `answered/` is the archive the prompt-contract grader reads.
- A request unanswered after `RELAY_TIMEOUT_SECONDS` (default 1700) is dropped with a 504.
  `GET /__relay/pending` lists the queue over HTTP.

`purpose` comes from the `X-Polyphony-Purpose` header that `app/llm/client.py` sends on
every call (`outline:skeleton`, `outline:beats`, `beat_prose`, `scene_revise`, …), falling
back to the system message's first line. Before the header, generation calls carried no
system message and were all filed under an empty purpose.

Point the app at the model relay through the stub provider (`LLM_PROVIDER=stub
STUB_LLM_API_KEY=x STUB_LLM_BASE_URL=http://127.0.0.1:8299/v1 LLM_TIMEOUT_SECONDS=1800`),
and the judge at a **separate** relay so the model under test never grades itself:

```bash
EVAL_JUDGE_BASE_URL=http://127.0.0.1:8298/v1 EVAL_JUDGE_LABEL=claude-judge \
LLM_TIMEOUT_SECONDS=1800 EVAL_POLL_SCALE=20 EVAL_RELAY_DIR=/tmp/relay-queue \
python -m evals.run --book cel --steps extraction,outline,continuity,prose,revise,attribution
```

`EVAL_POLL_SCALE` multiplies every harness poll timeout (default 1), because an agent
answers far slower than a vendor. The eval process also needs an `LLM_TIMEOUT_SECONDS`
long enough for an agent-paced judge. `EVAL_RELAY_DIR` here is the **model's** queue,
whose archive the prompt-contract grader reads.

**Declared runs are preflighted (SPEC-68).** A gated draw names its run, `--run gated-full |
gated-scenes | attribution` (`evals/preflight.py` `RUNS`), and `evals.run` checks it before the
first step. It stops with the reason if `--steps` is not that run's list, if `EVAL_RELAY_DIR` is
unset, if the relay's `answered/` or `EVAL_CACHE_DIR` holds a file, or if `GET /usage` reports a
`daily_token_limit` below the run's minimum ("daily limit N: run needs ≥ M"). Check a stack
before drawing with `python -m evals.preflight --run <name>`. There is no skip flag. A run
without `--run` (e2e, CI) is not preflighted and is never gated; the scorecard says
`run=undeclared`. The harness logs the eval user in again on a 401 and retries once, so the
app's 30-minute token carries a long run; the scorecard counts `relogin`.

## Regenerating a corpus

```bash
python -m evals.tools.rename   --book dracula --source /path/to/source.txt   # renamed excerpts
python -m evals.tools.build_gt --book dracula                                 # ground_truth.json
```

## Corpora

- **`dracula`** — the training corpus (epistolary; three diary/letter narrators).
- **`frankenstein`** — the **held-out validation** corpus (Walton's letters,
  Victor's creation chapter, the creature's quoted tale). A prompt/architecture
  change counts only if it improves **both**: a gain on Dracula alone is likely
  overfitting to one book's ground truth. CI grades both; Tracer keys each book
  as its own suite (`suite=dracula` / `frankenstein`), so they trend apart.
- **`cel`** — the synthetic fidelity corpus (the outline gate, and the harness v2
  book). `load_corpus` uploads the 17k-char `synopsis.txt` + the excerpt.
  `excerpts.txt` is ~4.7k chars of synthetic prose with tagged and inverted lines,
  action beats and an untagged exchange; `ground_truth.json` adds `characters`,
  speaker-labelled `gold_lines` (≥ 6 per principal, train/test), `other_lines`,
  `premise_edit` and five continuity injections planted in the excerpt;
  `style.json` is the style guide. Provenance in `corpora/cel/PROVENANCE.md`.

## Signed placements

`evals/placements/<run>.json` is a person's reading of where one run's outline plans each
`excerpt_events` event. It is the only placement a plot gate reads:

```json
{"run": "W6b", "outline_hash": "<plot.outline_hash(records)>", "signed_by": "PM", "signed_at": "2026-10-…",
 "placements": {"bell": {"chapter": 2, "line": "The Numbers Do Not Reconcile: …"},
                "drawer": {"chapter": null, "line": null}, …}}
```

Every event has a cell; `null` means "not planned in this outline", and a placed cell quotes
the scene line the signer read. `outline_hash` is the SHA-256 of
`json.dumps(outline_chapters(records), sort_keys=True)`, so whitespace in an answer is ignored.

Draft one with `python -m evals.graders.plot draft <answered-dir> --run W7` (a word-boundary
term match, written with `signed_by: null`). It refuses a directory with no `outline:beats`
record, such as `answered-archive/` instead of `answered-archive/model/answered`. The signer corrects the cells and sets `signed_by`
and `signed_at`. An unsigned file is not read. Without a signed file, the plot gates report
`null` and `placement_signed: false` (SPEC-54).

Regrade a run offline with `python -m evals.graders.plot regrade <run-dir>` (the dir holding its
`eval-report-<book>.json` and `answered-archive/`). It grades with the movement store the run's own
report holds (`steps.prose.voice.movement`); without one, `movement` is `null`. It also reads the
report's `steps.prose.voice.withheld` (each scene's grounding API `withheld` lines, per beat and
owner), so a block the product withheld because the plan carries it is `carried`, not starved,
and a starved block names the product's rule. `movement.per_principal` and
`unmatched_withheld` are reported; a row matching two beats is an error (SPEC-69). A row whose
`withheld_more` is above 0 (the record was capped) is never `carried` (W10-4). The regrade also
reports `outline_source_conflicts` (the outline step's "Outline vs your source: " warnings, with
their outline quotes) and `source_fact_hits` (per cel `source_facts` row: the signed placement's
`contradicted` cell, and whether a conflict names the fact; SPEC-73).
The voice instrument runs offline with `python -m evals.measure.voice_instrument` (SPEC-74).

## Adding a book — data only, no code

1. `corpora/<book>/aliases.json` — rename map + the `sections` to extract.
2. `corpora/<book>/spec.json` — cast, `voice_sources`, synopsis, canonical
   beats, continuity injections (see Dracula's for the shape).
3. Run `rename` then `build_gt` (above) and write `PROVENANCE.md`.

`voice_sources` knobs (each narrator needs a **disjoint, single-narrator**
section so voices separate):
- `{"chapters": [...]}` — whole chapters/letters of that narrator's text; or
  `{"blocks": "regex"}` — letter/diary block headers.
- `"quoted": true` — the narration is a nested multi-paragraph quotation (each
  paragraph opens with `“`, e.g. a character recounting their story); keeps that
  framed prose as voice instead of discarding it as dialogue.
- `"max_lines": N` — cap this narrator's gold lines to **balance the pool**; a
  narrator with far more lines than the others biases retrieval and understates
  separability.

Heading styles recognized by the slicer (`evals/tools/headings.py`): roman
`CHAPTER II`, arabic `Chapter 5`, and `Letter 4`. Extend that one file for a new
style. No other Python changes: `build_gt.py` reads `spec.json`, and the steps
read the resulting `ground_truth.json`. A new eval *step* is likewise one
`@step("name", needs_api=...)` decorator in `steps/pipeline.py` — the runner
discovers it from the registry.
