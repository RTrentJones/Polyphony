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
| `extraction`  | finds the right cast          | upload → extract → **F1** vs gold cast | yes (extract) |
| `ingestion`   | upload yields usable voice     | upload → per-character indexed-chunk count → **fraction grounded** | yes (extract) |
| `retrieval`   | voices separate in embedding space | held-out line → nearest across pooled voices → **precision@k / MRR** | no (embedder) |
| `attribution` | generated line sounds like the character | test-dialogue → embed → nearest centroid → **top-1 accuracy** vs chance | yes (generate) |
| `outline`     | recovers the story's shape    | generate outline (premise confirmed with the author's edit) → **R1 × mean(R6, R3)** + unknown-name rates | yes |
| `continuity`  | catches contradictions        | inject known errors → **detection recall**, precision, quote-verbatim + false-positive rate | yes |
| `prose`       | end-to-end scene quality      | the product path: outline → promote → planned scenes → deterministic cast/seam checks + **R1 × mean(R2..R5)** per scene | yes |

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
  cast holds two or more principals. When that path is unavailable it falls back to one scene from
  canonical beat 2. Each scene gets deterministic checks first (cast recall caps R1; a beat-seam or
  scene-seam 6-gram overlap caps R4; invented names), then R1–R5 from the judge for the first
  `EVAL_PROSE_JUDGE_SCENES` scenes (default 3). R2 sees each speaker's gold lines and the scene's
  spoken lines with tags and names removed. Beats come from the scene payload, else a read-only
  `SELECT` on `scene_beats` (`EVAL_DATABASE_URL`, default the app's `DATABASE_URL`), else the
  grounding descriptions; `beats_source` says which.
- **continuity** on `cel` injects its five planted contradictions into the excerpt (a synopsis is
  not prose).
- **prompt contract** grades what the app actually sent the model, from the relay archive (below).
  It is not a registered step: `evals.run` adds `steps.prompt_contract` when `EVAL_RELAY_ARCHIVE`
  (an `answered/` dir) or `EVAL_RELAY_DIR` (its `answered/` subdir) is set, and grades only
  exchanges archived after the run started. The checks are `beats_block`, `prior_paragraphs`,
  `voice_dialogue` (samples cross-checked against `gold_lines` / `other_lines`), `beats_stage`,
  `outline_no_repr` and `style_first`. Standalone:
  `python -m evals.graders.prompt_contract <answered-dir> --book cel`.

The rubric texts and score arithmetic are in `graders/rubrics.py`, the n-gram seam check in
`graders/seams.py`. A deterministic failure caps its rubric at 0 whatever the judge says.
Reports carry `app_sha` (the app's `/__version`, else the harness checkout's `git rev-parse
HEAD`) and `harness_sha`.

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
/ `LLM_MODEL`, default `gemini-2.5-flash`), keyed by the app's `/__version` sha,
so a prompt/architecture change shows as a step-score delta on the dashboard.

**CI:** `.github/workflows/evals.yml` boots a fresh app (pgvector service +
`GEMINI_API_KEY`), runs the suite, and ingests to Tracer — dispatchable on demand
(before/after a change) and weekly for a baseline. It needs `GEMINI_API_KEY` and
`TRACER_INGEST_TOKEN` as repo Actions secrets.

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
python -m evals.run --book cel --steps extraction,attribution,outline,continuity,prose
```

`EVAL_POLL_SCALE` multiplies every harness poll timeout (default 1), because an agent
answers far slower than a vendor. The eval process also needs an `LLM_TIMEOUT_SECONDS`
long enough for an agent-paced judge. `EVAL_RELAY_DIR` here is the **model's** queue,
whose archive the prompt-contract grader reads.

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
  overfitting to one book's ground truth. CI grades both and tags them as
  separate Tracer runs (`env=<label>-dracula` / `-frankenstein`).
- **`cel`** — the synthetic fidelity corpus (the outline gate, and the harness v2
  book). `load_corpus` uploads the 17k-char `synopsis.txt` + the excerpt.
  `excerpts.txt` is ~4.7k chars of synthetic prose with tagged and inverted lines,
  action beats and an untagged exchange; `ground_truth.json` adds `characters`,
  speaker-labelled `gold_lines` (≥ 6 per principal, train/test), `other_lines`,
  `premise_edit` and five continuity injections planted in the excerpt;
  `style.json` is the style guide. Provenance in `corpora/cel/PROVENANCE.md`.

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
