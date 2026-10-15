# SPEC-22 — Eval harness v2: an eval that tests the product

**Closes**: eval-plan §3.5 fixes 1–8 (cycle 2026-10, package W0-H) · **Size**: M ·
**Depends on**: — (merges first, so the "before" run uses the same harness as "after")

## Why

The relay run (orchestrator findings) showed that the `cel` eval under-tested the product:

1. The prose step asked for a generic "fog-bound street, at night" scene that contradicts the cel
   setting and ignores the outline the product just generated.
2. The cel canon was placeholder text ("X, from the corpus."), every principal was a protagonist,
   and the 389-char excerpt gave no character a voice sample, so voice grounding (R2) could not
   be tested.
3. There was no style guide, so style adherence (R4) could not be tested.
4. Continuity skipped on cel (no planted contradictions).
5. The judge graded itself: same provider, same relay queue as the model under test.
6. The relay filed every generation call under an empty `purpose`.
7. Reports carried an empty `app_sha`.
8. Nothing checked what the app actually SENT the model, which is the before/after signal that
   does not depend on model variance.

## Outcome

The cel eval writes into a book an author would have (real cast entries, a style guide, a
voice-bearing source), generates prose through the product's own path, grades each artifact on
the R1–R6 rubrics with a judge that is not the model under test, and reports deterministic seam,
cast and prompt-contract numbers that a relay run can compare before and after a change.

## Scope

**In**: `evals/steps/pipeline.py`, `evals/harness/{client,judge,config,report}.py`, `evals/run.py`,
`evals/relay_llm.py`, `evals/corpora/cel/{ground_truth.json,excerpts.txt,style.json,PROVENANCE.md}`,
new `evals/graders/{rubrics,seams,prompt_contract}.py`, `app/llm/client.py` (one header).
**Out**: `evals/stub_llm.py` (W1-D), any product behaviour. The prompt-contract checks are expected
to FAIL on the base commit; the wave-1 packages make them pass.

## Design

### Corpus (cel)
- `ground_truth.json` gains `characters` (Milo Voss = protagonist, Zara Okafor = deuteragonist
  (mentor), Edric Thane = antagonist "the CEL", each with description, goals, voice and aliases),
  `gold_lines` (speaker-labelled quoted lines, ≥ 6 per principal, train/test split),
  `other_lines` (the caseworker, Old Fen), `premise_edit` (the author's premise for R6),
  `continuity_source: "excerpts"` and five `continuity_injections`.
- `excerpts.txt` is ~4.7k chars of synthetic cel prose (provenance in `PROVENANCE.md`) with tagged
  and inverted lines, action beats, an untagged multi-paragraph exchange, and the facts the five
  contradictions break: Zara's grey eyes (→ brown), Halvard Bell sent under then speaking
  (dead speaks), the ledger locked in a drawer then the Records vault (object in two places),
  "Two days after" Tuesday (→ before: timeline inversion), Edric → "Edwin" Thane (broken alias).
- `style.json`: POV third limited on Milo, past tense, tone, comps, two paragraphs of sample
  prose, dialogue-forward. Every eval book gets it through `PUT /books/{id}/canon/style`
  (`client.set_style`).

### Steps
- `_cast_with_roles` uses `characters` entries when a corpus has them (role, description, goals,
  `voice_characteristics.voice`, aliases); the placeholder only remains for corpora without them.
- **outline**: confirms the premise checkpoint WITH `premise_edit` (`generate_outline(premise=)`
  sets `confirm_premise` and posts the edit). Judged on R1, R6, R3 with the canon block
  (`Canon.full_block()` rebuilt from `GET /books`, `/characters/{id}`, `/books/{id}/canon`), the
  confirmed premise (plain text) and the chapter count requested. `score = R1 × mean(R6, R3)`;
  a missing principal caps R1 at 0. Reports `unknown_rate` (over `fidelity.known`, unchanged) and
  `unknown_rate_canon` / `unknown_canon` (over canon + every synopsis proper noun, with words the
  text also uses in lower case treated as sentence starts), so the outline-warning fix (W1-F) is
  measurable.
- **prose**: the product path. Upload the excerpt to the book and commit the canon cast by name
  (voice indexing runs; curated entries are untouched) → outline (memoised from the outline step;
  stored into this book with `PUT /plans` as names, since ids are per book) → `promote-all` →
  the first chapter whose cast holds ≥ 2 principals → `generate-planned` → wait for the chain →
  each scene with its beats. Fallback, when the product path is unavailable: one scene from
  canonical beat 2 at "Reconciliations floor, Aeon Holdings, Marrow District".
  Per scene: deterministic cast recall (caps R1), beat-seam and scene-seam 6-gram overlap (caps
  R4), name invention; then R1–R5 by the judge (`EVAL_PROSE_JUDGE_SCENES`, default 3 scenes).
  R2 sees each speaker's gold lines and the scene's spoken lines with tags and names removed
  (`pipeline.strip_tags`). `scene score = R1 × mean(R2..R5)`; the step score is the mean.
  Beats come from the scene payload when the API exposes them, else a read-only `SELECT` on
  `scene_beats` (`EVAL_DATABASE_URL`, default the app's `DATABASE_URL`), else descriptions only
  from `/scenes/{id}/grounding` (`beats_source` says which).
- **continuity**: on cel, the injected/control text is the excerpt (a synopsis is not prose).
  Adds `precision` (findings pointing at a planted error) and `quote_verbatim`.
- **prompt contract** (not a registered step; `evals.run` adds `steps.prompt_contract` when
  `EVAL_RELAY_ARCHIVE` or `EVAL_RELAY_DIR` is set, grading only exchanges archived after the
  run started): `beats_block`, `prior_paragraphs`, `voice_dialogue` (cross-checked against
  `gold_lines` / `other_lines`), `beats_stage`, `outline_no_repr`, `style_first`. CLI:
  `python -m evals.graders.prompt_contract <answered-dir> --book cel`.

### Judge and relay
- `EVAL_JUDGE_BASE_URL` (+ `EVAL_JUDGE_LABEL`, `EVAL_JUDGE_MODEL`, optional
  `EVAL_JUDGE_API_KEY`) sends the judge to its own OpenAI-compatible endpoint, bypassing the
  provider chain. The report's `judge` block carries `endpoint`, and `self` is false unless the
  URL equals `EVAL_APP_LLM_BASE_URL`. The scorecard prints `@ <endpoint>`.
- `app/llm/client.py` sends `X-Polyphony-Purpose: <purpose>` on every call; `relay_llm` files
  each request under that header, falling back to the system line.
- `app_sha` falls back to `git rev-parse HEAD` when `/__version` reports none; `harness_sha` is
  always recorded.

## Running the before/after (eval plan §3)

```bash
# model relay on :8299 (the app), judge relay on :8298 (the eval process only)
EVAL_JUDGE_BASE_URL=http://127.0.0.1:8298/v1 EVAL_JUDGE_LABEL=claude-judge \
STUB_LLM_BASE_URL=http://127.0.0.1:8298/v1 LLM_TIMEOUT_SECONDS=1800 EVAL_POLL_SCALE=20 \
EVAL_RELAY_DIR=/tmp/claude-0/relay-queue \
python -m evals.run --book cel --steps extraction,attribution,outline,continuity,prose
```

The eval process needs `LLM_TIMEOUT_SECONDS` long enough for an agent-paced judge.

## Acceptance

| Criterion | Test |
|---|---|
| `_cast_with_roles` returns the real cel descriptions | `test_evals_steps::test_cel_cast_is_the_real_canon` |
| Prose builds its request from a planned beat (client mocked) | `test_evals_steps::test_prose_writes_the_planned_scenes_of_a_two_principal_chapter`, `…falls_back_to_canonical_beat_two`, `…reuses_the_memoised_outline_in_its_own_book` |
| `seams.max_overlap` ≥ 1 on a copied sentence, 0 when clean | `test_evals_seams` |
| `prompt_contract` flags a prompt missing `<beats>` (and the other contracts) | `test_prompt_contract` |
| Relay purpose header round-trip | `test_relay_llm::test_the_purpose_header_names_the_request`, `test_llm_client::test_purpose_rides_a_header` |
| Judge reports `self=false` with `EVAL_JUDGE_BASE_URL` | `test_evals_judge::test_a_judge_at_its_own_url_is_not_self_grading`, `…calls_that_url_and_reports_it` |
| Outline: premise edit confirmed, R1 capped on a missing principal, synopsis names allowed | `test_evals_steps::test_outline_confirms_the_authors_premise_and_caps_r1` |
| cel: voice-bearing excerpt, five contradictions in the excerpt window | `test_evals_steps::test_cel_excerpt_carries_speaker_labelled_voice`, `…plants_five_contradictions…`, `…continuity_runs_on_the_excerpt_for_cel` |
| Corpus schema unchanged for the embedding test | `tests/integration/test_evals_corpus.py` |

## BRD / feature-set delta (for the orchestrator)

- BRD §7 (evaluation): the cel eval runs the author path (outline → premise confirmation →
  promote → planned scenes) and grades R1–R6 with a separate judge; deterministic prompt-contract
  and seam checks over the relay archive are the primary before/after signal.
- feature-set, Evals row: "rubric judge (R1–R6) with a separate judge endpoint; prompt-contract
  grader; seam grader; cel corpus with canon, style guide, voice excerpt and planted
  contradictions".
