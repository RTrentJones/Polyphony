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
| `outline`     | recovers the story's shape    | generate outline → structural + **judge** vs canonical beats | yes |
| `continuity`  | catches contradictions        | inject known errors → **detection recall** + false-positive rate | yes |
| `prose`       | end-to-end scene quality      | generate scene → **judge** rubric | yes |

`retrieval` (and the reference side of `attribution`) run in-process with the
app's own fastembed model — **free, no LLM, no server**. On the committed Dracula
corpus these score p@3 ≈ 0.82 / attribution ≈ 0.82 against a 0.33 chance baseline.

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
