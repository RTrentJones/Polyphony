# SPEC-08 — Incremental resume for multi-call jobs

**Closes**: F12 · **Size**: M · **Depends on**: SPEC-07

## Why

BRD R7.2 says a scene is a single-call job that resumes without duplicate spend. A prose scene is
one planner call plus one call per beat, and an ensemble scene is several calls per beat. On
resume, both restart from the planner and re-spend every completed beat; the staged outline
likewise restarts at the skeleton. On the free tier, where pauses are routine, this turns a
quota edge into a quota multiplier.

## Outcome

A resumed scene or staged outline continues from the last completed LLM call. The BRD's
no-duplicate-spend claim becomes true for every job kind, and the stage/beat progress that the
checkpoints record is shown to the author.

## Scope

**In**: prose and ensemble scene workflows, the staged outline handler, a checkpoint store.
**Out**: checkpointing inside a single LLM call (not possible).

## Design

1. **Checkpoint store.** Add `jobs.checkpoint JSONB` (job-scoped, deleted with the job) rather than
   reusing domain tables, so a cancelled or failed job leaves no half-written domain state.
   `repository.save_checkpoint(job_id, data)` commits in its own short transaction.
2. **Scenes.** Checkpoint after the planner (`{"beats": [...]}`) and after each beat
   (`{"beats": [...], "prose": [...], "beat_index": n}`). On start, a workflow given a checkpoint
   skips completed calls. `SceneBeat` rows are still written only at completion, so the domain
   stays all-or-nothing.
3. **Staged outline.** Checkpoint after the skeleton, after chapters, after each act's beats, and
   after the repair pass. The audit is deterministic and needs no checkpoint.
4. **Canon drift.** Store a hash of `canon.full_block()` in the checkpoint. If the canon changed
   while the job was paused, discard the checkpoint and restart, and say so in the job's `error`
   field as an informational note, because mixing stages generated against two canons would be
   worse than paying twice.
5. **Progress.** Expose `progress: {done, total}` on the job API (SPEC-07) from the checkpoint, so
   the UI can show "beat 3 of 5" or "act 2 of 4".
6. **Handler payloads** stop carrying derived data that can go stale across a pause
   (`chapter_summary`, `prior_tail` are recomputed at start from the DB).

## Acceptance

1. A prose scene whose third beat raises `QuotaExhaustedError` resumes and makes exactly
   `beats_total − 2` further beat calls and no planner call. *(unit, call-counting stub)*
2. A staged outline paused after the chapters stage resumes without calling the skeleton or
   chapters stage again. *(unit)*
3. If a character is edited during the pause, the resumed job restarts from scratch and records why.
   *(unit)*
4. A cancelled job's checkpoint is deleted and no `SceneBeat` rows exist for its scene. *(unit)*

## Docs

BRD R7.2: replace the "single-call vs multi-call" scope note with "every job resumes from its last
completed model call; a canon change during a pause restarts the job".
