# SPEC-07 — Job lifecycle: visible pauses, bounded waits, cancel, idempotency

**Closes**: F11, F13, F14 · **Size**: M · **Depends on**: SPEC-03 (pause classification)

## Why

The job system is sound, but its states do not reach the author consistently. Only scenes reflect a
pause; a paused outline, extraction or continuity check looks stuck. A pause has no ceiling and no
cancel. Two LLM paths still run inside the HTTP request and can outlive the proxy timeout. Nothing
stops a second identical job being enqueued while the first is in flight.

## Outcome

Every LLM job has one observable lifecycle (`queued → running → paused ↔ queued → succeeded |
failed | cancelled`) exposed through one endpoint and mirrored onto its domain row. Pauses stop
retrying after a bounded window and tell the author when work will resume. The author can cancel.
Duplicate enqueues return the in-flight job instead of creating another. No LLM call runs in a
request handler unless it is bounded below the proxy timeout.

## Scope

**In**: `app/jobs/*`, every handler's hooks, the plan, extraction-run and continuity-report status
vocabularies, `generate_plan`'s sync fallback, `test-dialogue`, the frontend status badges and
polling.
**Out**: concurrency beyond one worker (BRD §8 keeps it out of scope).

## Design

1. **Job status API.** `GET /api/v1/jobs/{id}` (owner-scoped) returns `{kind, status,
   available_at, attempts, error, domain: {type, id}}`. Every enqueueing endpoint already returns
   or can return `job_id`; make that universal.
2. **Domain mirroring.** Every handler registers `on_pause` and `on_dead`, and a new `on_resume`
   that the worker calls when it claims a previously paused job, so the domain row moves
   `paused → processing/generating` again. Status vocabularies gain `paused` for `BookPlan`,
   `ExtractionRun` and `ContinuityReport`. Add `resume_at` to the domain payloads so the UI can say
   "resumes around 14:05".
3. **Bounded pause.** Add `Job.paused_since`. When a job has been paused for longer than
   `JOB_MAX_PAUSE_HOURS` (default 48), it goes `dead` with error "quota did not return within 48h"
   and `on_dead` runs. Record `pause_count` for observability.
4. **Cancel.** `POST /api/v1/jobs/{id}/cancel` sets a `queued` or `paused` job to `cancelled` and
   runs a new `on_cancel` hook (domain row → `cancelled`, or back to its prior state for a plan
   whose previous content is intact). A `running` job gets `cancel_requested=true`; workflows check
   it between LLM calls (the same seams SPEC-08 checkpoints at) and stop.
5. **Idempotency.** Enqueue helpers take a `dedupe_key` (for example `outline:<book_id>`,
   `extract:<source_id>`, `continuity:<book_id>:<chapter_id|book>`); a partial unique index on
   `jobs(dedupe_key) WHERE status IN ('queued','running')` makes a second enqueue return the
   existing job (HTTP 200 with the existing `job_id`, not 409). Scene generation dedupes on a
   client-supplied `Idempotency-Key` header, since two scenes in one chapter are legitimate.
   Voice indexing (`index_characters_voice`) uses a MERGING key, `voice:<book_id>:<source_id>`
   (`jobs_repo.enqueue_merging`): a plain dedupe would drop newly approved character ids, so a
   still-queued (incl. paused) holder gets the new ids unioned into its payload, and a running
   holder releases the key and keeps running while a fresh job takes the new ids.
6. **No unbounded LLM in requests.** The single-call outline fallback becomes a `generate_outline`
   job with `mode="single"`. `test-dialogue` stays synchronous (fast model, one call) but runs with
   a 45 s total budget (SPEC-03 step 5) and calls `check_user_budget`.
7. **Frontend.** One `useJob(jobId)` hook polls the job endpoint with backoff, and badges render
   `queued`, `running`, `paused (resumes ~HH:MM)`, `failed`, `cancelled` identically across
   scenes, outline, extraction and continuity, each with a Cancel button while cancellable.

## Acceptance

1. For each job kind, a stubbed `QuotaExhaustedError` moves the domain row to `paused`, and the
   resumed run moves it back to in-progress and then to its terminal state. *(unit, parametrised
   over `HANDLERS`)*
2. A job paused for longer than `JOB_MAX_PAUSE_HOURS` goes dead and its domain row `failed` with
   the quota message. *(unit with a frozen clock)*
3. Cancelling a queued outline leaves the previous plan content and status intact. *(unit)*
4. Two `generate_plan` calls in a row produce one job and the same `job_id`. *(unit)*
5. No route handler in `app/api/` awaits `get_llm_client().generate` other than `test-dialogue`.
   *(grep-based unit test)*

## Docs

BRD R7.2 gains "a pause is visible on the domain object with an expected resume time, is bounded,
and is cancellable". Feature set §10 adds the jobs API row.
