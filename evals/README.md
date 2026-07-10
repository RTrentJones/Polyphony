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

## Judge

Rubric-scored steps (`outline`, `prose`) use a pluggable LLM judge — default
**Gemini** (the only key present today). Set `EVAL_JUDGE_PROVIDER=anthropic|groq|
openai` (+ its key) to grade with a different family; the report flags
self-grading when the judge equals the model under test. Judge calls are
temperature 0.

## Regenerating a corpus

```bash
python -m evals.tools.rename   --book dracula --source /path/to/source.txt   # renamed excerpts
python -m evals.tools.build_gt --book dracula                                 # ground_truth.json
```

## Adding a book

Create `corpora/<book>/aliases.json` (rename map + chapters), add a `SPEC` entry
in `tools/build_gt.py`, run the two tools above, and write `PROVENANCE.md`.
