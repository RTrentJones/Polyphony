# SPEC-06 — Eval parity with the product, and the fidelity gate in CI

**Closes**: F5 · **Size**: M · **Depends on**: — (do this first; it makes every other spec
measurable)

## Why

Three eval steps can only score zero against the current app, and the gate the BRD names as its
acceptance test does not exist.

- The outline step reads `content` from `POST /plans/generate`, which now returns a job handle.
  Its eval book also has no characters, so the principal gate is vacuous.
- The extraction and ingestion steps read the source's characters straight after upload, but
  characters now exist only after a reviewed extraction commit, which the harness never performs.
- Offline extraction grades the legacy `CharacterExtractor`; the product uses `extract_canon`.
- `cast_fidelity` is computed only offline, the `cel` corpus is not in the CI matrix, and nothing
  fails on fidelity.

Until this is fixed, a regression in exactly the property the BRD was written to protect would
ship green.

## Outcome

Every API-mode eval step drives the same flow an author does, the `cel` corpus runs in CI, and a
deterministic `cast_fidelity` check on a stubbed model gates every PR, with the live-model version
gating the eval workflow.

## Scope

**In**: `evals/harness/client.py`, `evals/steps/pipeline.py`, `evals/offline.py`,
`.github/workflows/evals.yml`, `.github/workflows/test.yml`, a new unit test.
**Out**: new graders beyond wiring the existing fidelity grader; judge-provider work.

## Design

1. **Client helpers.** Add `wait_plan(book_id, kind)` (poll `GET /books/{id}/plans` until the plan
   is `ready` or `failed`), `wait_extraction(book_id, run_id)` (poll until `ready`/`failed`),
   `commit_extraction(book_id, run_id, proposals)` (approve everything as proposed), and
   `book_canon(book_id)`.
2. **Extraction step.** Upload → wait for the run → grade the *proposed* character names (this is
   what extraction produces) → commit → assert that the committed cast equals the proposals.
   Report proposal F1 as the score and commit fidelity as a guard field.
3. **Ingestion step.** Same as above through commit, then wait for the
   `index_characters_voice` job (poll character `indexed_at`), then measure grounded fraction.
4. **Outline step.** Create the book with the corpus synopsis **and** the corpus cast (with roles
   from `spec.json`, so principals are real), call generate, `wait_plan`, then grade with both the
   judge and `grade_fidelity`. The score becomes `cast_fidelity × judge` when the corpus declares
   `fidelity`, matching the offline formula.
5. **Offline parity.** `offline_extraction` calls `extract_canon` and grades its character
   proposals. `offline_outline` calls `generate_staged_outline` with a `Canon` built from the corpus
   (stubbing nothing), so offline and API measure the same generator.
6. **CI matrix.** `evals.yml` default books become `dracula frankenstein cel`; `cel` runs only the
   `outline` step by default to save quota. A job-level check fails the workflow if `cel`
   `cast_fidelity < 0.9`.
7. **Free deterministic gate on every PR.** A unit test (`tests/unit/test_fidelity_gate.py`)
   builds the `cel` `Canon` from `evals/corpora/cel`, runs `generate_staged_outline` with a stub
   client that returns the committed "Elara outline" fixture for the beat stage, and asserts the
   audit fails and triggers exactly one repair; a second stub returning a faithful outline passes.
   This runs in `test.yml` with no key.
8. **Prompt-content gate.** A unit test assembles each generator's prompt (outline stages, beat
   prose, beat planner, continuity, extraction, ensemble narrator and editor) from the `cel`
   fixture and asserts the synopsis tail is present wherever the canon is supposed to be. It is the
   BRD §7 "full synopsis reaches the model" gate, extended to every generator.

## Acceptance

1. Against a locally booted app with a stub provider (a tiny FastAPI fake of the OpenAI-compatible
   endpoint returning canned JSON), the outline, extraction and ingestion steps return non-zero
   scores. *(integration test under `RUN_PG_TESTS`)*
2. `python -m evals.offline --book cel --steps outline` exercises `generate_staged_outline`.
   *(unit: patch and assert the call)*
3. `test.yml` runs `test_fidelity_gate.py` and `test_prompt_content_gate.py` on every PR without
   secrets.
4. `evals.yml` fails when the `cel` outline step reports `cast_fidelity < 0.9`. *(verify once by
   dispatching against a branch that reintroduces a 2,000-char cap; record the run link in the PR)*

## Docs

`evals/README.md` step table updated (extraction grades proposals; outline waits for the job; `cel`
gate). Skill "Verifying a generation change" points at the two new unit tests and the workflow gate.
Feature set §9 rows marked done.
