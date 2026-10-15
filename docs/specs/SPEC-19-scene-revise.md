# SPEC-19 — AI revision of a scene (backend)

**Closes**: backlog B3 (API + job; the editor UI is W2-A) · **Size**: L · **Depends on**: SPEC-07,
SPEC-08, SPEC-12

## Why

Drafting is supported end to end; revising is not. The editor offers a textarea, Save and a
revision list. No route revises or regenerates a scene or a beat; `PUT /scenes/{id}/content` is the
only way to change prose. An author who wants "tighten this" or "make Zara colder" must rewrite it
by hand or regenerate the whole scene.

## Outcome

The author asks for a revision of a range, a beat, or the whole scene. A job writes it as a
**proposal** next to the draft. The draft does not change until the author accepts the proposal,
and the text outside the range is byte-identical by construction.

## Scope

**In**: `POST /books/scenes/{id}/revise`, the `revise_scene` job, the revise prompt, proposals
(`SceneRevision(source="ai_revise")` + `meta`), accept, discard, `GET .../proposals`, the revise
estimate, the stub LLM's revise family. **Out**: the editor UI (diff view, Accept/Discard buttons)
is W2-A.

## Design

### API (`app/api/revise.py`, mounted at `/api/v1/books`)

| Route | Does |
|---|---|
| `POST /scenes/{id}/revise` | Body `{instruction (1..2000), range?: {start, end}, beat_index?, base_text_hash?, selected_text?}`. At most one of `range` / `beat_index`; neither means the whole scene. Returns `{job_id, scene_id, status, deduplicated, range, estimate}`. |
| `GET /scenes/{id}/proposals` | `{draft, draft_hash, proposals: [{id, content, instruction, range, revised_range, beat_index, job_id, status, warnings, stale, …}]}`, newest first. |
| `POST /scenes/{id}/revisions/{rid}/accept` | Snapshots the current draft (`edited`, or `generated` if the scene was never edited, the same rule as `PUT /content`), sets the draft to the proposal, `meta.status = "accepted"`. |
| `POST /scenes/{id}/revisions/{rid}/discard` | `meta.status = "discarded"`. Nothing else changes. |
| `POST /estimate` `{action: "revise", scene_id, range? \| beat_index?}` | The same estimate the revise endpoint enforces. |

- **The draft** is `Scene.draft` (`app/core/orm_models.py`): edited content, else the
  generation, else `""`, **raw** (edge whitespace kept). It is the one definition: the editor
  loads it (`GET /scenes/{id}` `content`), `PUT /content` and accept snapshot it, the job splices
  it, and the export prints it trimmed (`scene_text` = `scene.draft.strip()`).
- **Snapshots** are one function, `books.snapshot_draft(db, scene)`: a manual save and an accept
  both call it, so the rule (`edited` once the author has saved over the generation, else
  `generated`) cannot diverge.
- **The span** is one model and one resolver: `SpanSpec` (`range` + `beat_index` + the one-of
  rule, in `app/orchestration/revise.py`) is the base of both `ReviseRequest` and the estimate's
  `EstimateRequest`, and `resolve_scene_span(db, scene, spec) -> Span` resolves it for both
  endpoints. A beat span carries its `SceneBeat.description`.
- **Layering:** API → orchestration ← job. `app/api/*` never imports `app.jobs.revise`; the job
  kind (`JOB_KIND`), `cast_request`, `SpanSpec` and `resolve_scene_span` live in
  `app/orchestration/revise.py`. **Offsets** are Python string indices
  (Unicode code points) into `draft` as `GET /proposals` returns it, end exclusive. A JS string
  index counts UTF-16 code units, so W2-A converts a selection with
  `Array.from(text.slice(0, i)).length`. W2-A should also send `selected_text` and
  `base_text_hash` (= `draft_hash`): a stale view, or an offset counted in the wrong unit, is then
  refused with a 409 instead of revising the wrong words.
- **Beat mode** finds `SceneBeat(beat_number = beat_index).content` verbatim in the draft. If it is
  absent or ambiguous, it returns 409 "The beat was edited; select the text instead." An unknown
  beat returns 404.
- **Preflight**, as every LLM endpoint does: ownership through the book (`owned_scene`), 409 while
  the scene is still generating, `check_user_budget`, and `estimates.revise()` + `assess()`. Over
  budget is refused with 429 and the estimate. The dedupe key `revise:<scene_id>` allows one
  revision in flight per scene; a repeat returns the running job with `deduplicated: true`.
- **Accept** returns 409 if the draft changed since the proposal was written (`meta.base_text_hash`),
  and 409 if the proposal is not `proposed`. Accepting twice, or accepting after a discard, is
  refused.

### Job (`app/jobs/revise.py`, kind `revise_scene`, `domain = ("scene", id)`, `max_attempts=1`)

Payload `{scene_id, user_id, book_id, instruction, range: [start, end], beat_index,
beat_description, base_text_hash}`. The span is resolved at the API, so the job always works on a range.

1. Re-read the scene, the chapter summary and the canon. If the draft's hash differs from
   `base_text_hash`, fail with "The draft was edited after this revision was requested…". The
   range would point at different text.
2. Make one model call (purpose `scene_revise`), then `ctx.save({"revised": …})`. That checkpoint
   is the cancel seam, so a cancel during the call stores nothing.
3. Splice `before + revised + after`, keeping the span's own leading and trailing whitespace. Run
   the deterministic name check, then re-check the hash and store the proposal with
   `meta = {instruction, range, revised_range, beat_index, job_id, status: "proposed",
   base_text_hash, warnings, unknown_names}`.

Hooks: `on_pause` and `on_resume` are no-ops. The scene's status describes its generation, and a
revision in flight must not show a completed scene as "paused"; the job API carries the pause and
its resume time. `on_dead` and `on_cancel` mark this job's still-`proposed` proposal, if one
exists, as `failed` or `cancelled`, and never touch an accepted or discarded one.
`QuotaExhaustedError` is not caught, so the worker pauses the job (R7.2).

### Prompt (`app/orchestration/revise.py`)

The prompt opens with "Revise the TARGET passage…" (the stub's marker). Then come
`STORY_MATERIAL_NOTICE`, with one stated exception for the `<instruction>` block (the author's
request), the whole `Canon.full_block()`, the chapter summary `<chapter>`, and the scene's cast
via `build_cast_context` (bible and voice samples, retrieved for instruction + target). Next is
the POV line, `Point of view: <Pov.label>` (`prose.resolve_pov`, SPEC-24), and, for a beat
revise, `Beat <beat_index + 1> of this scene's plan: <SceneBeat.description>` right after it, so
"redo beat 2" tells the model what beat 2 was for (eval finding 6). A range or whole-scene revise
has no plan line. Then the text before the span as read-only `<prior>`,
the `<target>`, the text after as read-only `<after>`, and the `<instruction>`. Everything is
fenced with `as_quoted_block`, and nothing is truncated: prior + target + after is the whole draft.

### Cast fidelity (warn only, canon invariant 4)

`new_names` is one call, `unknown_proper_nouns(revised, canon_terms, canon_text=draft)` (the
outline audit's check, W1-S7): the draft's capitalised phrases count as known and the words it
uses in lowercase as common words, so "Bartholomew Quince" is reported and "Outside" (capitalised
in the draft) or "Numbers" (lowercase in the draft) is not. A one-word capital that only opens
sentences ("Silence fell.", "Nobody answered.", "Fine," she said) is not reported either
(wave 2.5, NB-1). The revise path has no rules of its own. Findings go
to `meta.warnings` / `meta.unknown_names`, and the proposal is never failed.

### Schema

Migration `0018_b3_scene_revise.py` (`b3_scene_revise` ← `spec10_outline_handoff`) adds the
nullable JSONB column `scene_revisions.meta` (JSON on sqlite). The migration is postgres-only and
guarded. Downgrade drops the column, and proposals stay as plain revisions.

## Acceptance

| Criterion | Test |
|---|---|
| Endpoint enqueues one job and dedupes | `tests/unit/test_scene_revise.py::test_revise_enqueues_one_job_and_dedupes` |
| Over budget → refused with the estimate | `…::test_revise_is_refused_with_the_estimate_when_over_budget` |
| Span validation (both modes, bad range, blank instruction, stale hash, selection mismatch) | `…::test_revise_validates_the_span` |
| Range splice keeps outside text byte-identical | `…::test_splice_keeps_everything_outside_the_span_byte_identical`, `…::test_revise_then_accept_keeps_outside_text_and_snapshots_once` |
| Beat mode locates the beat; edited beat → 409 | `…::test_beat_mode_locates_the_beat_and_refuses_an_edited_one` |
| Accept creates exactly one `edited` revision and sets content | `…::test_revise_then_accept_keeps_outside_text_and_snapshots_once` |
| Discard leaves `scene.content` byte-identical | `…::test_discard_leaves_the_draft_byte_identical` |
| Accept after a concurrent edit → 409 | `…::test_accept_after_a_concurrent_edit_is_a_conflict` |
| Prompt has full canon, cast context, `<prior>`, `<after>` | `…::test_prompt_carries_canon_cast_and_read_only_surroundings` |
| `QuotaExhaustedError` pauses, then resumes (through the worker) | `…::test_quota_exhaustion_pauses_then_resumes_without_touching_the_draft` |
| Cancel marks the proposal cancelled; cancel mid-call stores nothing | `…::test_cancel_and_dead_hooks_mark_this_jobs_proposal_only`, `…::test_cancel_during_the_call_stores_nothing` |
| No new named character (deterministic warning) | `…::test_an_invented_name_is_a_warning_not_a_failure`, `…::test_new_names_warns_on_an_invented_name_only`, `…::test_new_names_ignores_ordinary_sentence_openers` |
| One draft (raw), trimmed only by the export | `…::test_scene_draft_is_raw_and_the_export_trims_it` |
| Save and accept snapshot by one rule | `…::test_save_and_accept_snapshot_the_draft_by_one_rule` |
| `SpanSpec` refuses range + beat for revise and estimate | `…::test_span_spec_takes_a_range_or_a_beat_never_both` |
| Beat revise names the beat from the plan; a range revise does not | `…::test_a_beat_revise_prompt_names_the_beat_from_the_plan`, `…::test_prompt_carries_canon_cast_and_read_only_surroundings` |
| `GET /scenes/{id}` returns `beats` | `tests/unit/test_book_scenes.py::…::test_get_scene_returns_its_beats_in_order` |
| Migration upgrade/downgrade on PG | `tests/integration/test_pg_scene_revise.py::test_migration_upgrade_downgrade_round_trip` |
| Revise → accept through the worker on PG | `…::test_revise_accept_round_trip_through_the_worker` |
| Judge: instruction adherence ≥ 0.8, R5 continuity ≥ 0.8 | after-eval §3.4 (orchestrator) |

### Scene read (`GET /api/v1/scenes/{id}`, W1-S9)

The response adds the beats the scene was written in, loaded with the scene (no per-beat query):

```text
"beats": [{"beat_number": int, "description": str | null, "content": str | null}, ...]
```

ordered by `beat_number`; `[]` when the scene has no `SceneBeat` rows. `content` is the raw
`Scene.draft` (null when empty). The eval harness reads beat prose from here, never from the
database.

## Docs (delta for the orchestrator to apply)

**BRD §5.4**, a new requirement after R4.3:

> **R4.9** **AI revision proposes; the author decides** (SPEC-19). `POST /books/scenes/{id}/revise`
> revises a range, a beat, or the whole scene as a job. The job is preflighted and estimated,
> deduped per scene, pauses on quota and can be cancelled. Its result is a proposal
> (`SceneRevision(source="ai_revise")`) spliced server-side, so text outside the range is
> byte-identical. The draft changes only on accept, which snapshots the previous draft first and is
> refused if the draft changed since the proposal was written. Discard changes nothing. The revise
> prompt carries the whole canon, the scene's cast samples, and the rest of the draft as read-only
> `<prior>`/`<after>`. A name the revision introduces that is outside the canon and the scene is a
> warning, never a failure.

**feature-set.md**: add a row under scene generation (§3):

> | AI revision of a range / beat / scene | R4.9 | ✅ (API) | `POST /books/scenes/{id}/revise` → `revise_scene` job → proposal; `GET …/proposals`, `POST …/revisions/{rid}/accept\|discard`; `estimate` action `revise`. Editor UI: W2-A (SPEC-19) |
