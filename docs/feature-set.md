# Polyphony — Feature set

The catalogue behind [BRD.md](BRD.md). Requirement IDs (`R1.1`…) refer to BRD §5; decision numbers
refer to [ADR-002](ADR-002-book-as-root.md). Status reflects the book-as-root effort, not the
product's whole history.

**Status as of 2026-10-12** (reconciled against the code; see [docs/specs](specs/README.md)).

**Legend** — ✅ shipped · 🚧 in flight · ⬜ planned · ❌ deliberately not doing

---

## 1. Canon (the book's authored truth)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Book: title, author, genre, synopsis, status | — | ✅ | `synopsis` is unbounded `Text`; needs an editor sized for real prose (R5.1) |
| Characters scoped to exactly one book | R3.1 | ✅ | `characters.book_id NOT NULL ON DELETE CASCADE` (baseline 0001, PR #19) |
| Scenes scoped to exactly one book | R3.1 | ✅ | `scenes.book_id NOT NULL CASCADE` (SPEC-04); chapter delete moves or deletes its scenes, never orphans them |
| Character names unique within a book | R3.2 | ✅ | `uq_characters_book_name`, partial on `deleted_at IS NULL` since SPEC-13 |
| Character bible fields (role, goals, arc, relationships, traits, voice, notes) | — | ✅ | Exist on the model; `goals`/`arc`/`relationships`/`notes` are **dropped** by the outline's bible renderer |
| Canon entries (categorised worldbuilding) | — | ✅ | `canon_entries` (migration 0003); validated categories (SPEC-09) |
| Style guide (POV, tense, tone, comps, sample prose) | — | ✅ | `style_guides`, one per book; reaches every generator via `Canon` (SPEC-01) |
| Free-form notes | — | ✅ | A `Source` with `kind='paste'` (PR #23) |
| Canon fully viewable + editable | R5.1 | ✅ | Canon tab with version history (PR #25) |
| Cross-book character reuse | — | ❌ | Explicit copy, not a shared entity (BRD §2.2) |

## 2. Sources (raw input)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Upload a document (docx/pdf/txt/md) | — | ✅ | Currently `Manuscript`, user-scoped |
| `Manuscript` → `Source`, scoped to a book | — | ✅ | ADR-002 §2; you upload **into a book** (PR #19, #29) |
| Paste raw text as a source | — | ✅ | `kind='paste'` (PR #23) |
| Deleting a source never deletes the cast | — | ✅ | `characters.source_id ON DELETE SET NULL` and no ORM cascade; deletes are soft and restorable (SPEC-13) |
| Per-user content-hash dedupe | — | ✅ | Deliberately per-user: a global unique leaks a cross-tenant existence oracle (migration `0003`) |
| Extraction → **proposed** canon, reviewed per item | R4.4 | ✅ | Windowed map-reduce at manuscript scale (SPEC-09); never pre-approves over the author's synopsis or style |
| Extraction at manuscript scale (map-reduce) | R4.4 | ✅ | SPEC-09: ~12k-token windows, alias-aware merge, batched full entries, synopsis last; truncated window retried smaller, never fails the run; per-window checkpoint |
| Extraction cost preflight | R4.4, R7.3, R7.5 | ✅ | Estimate returned on enqueue; refused with "first N chapters" when it cannot finish within `JOB_MAX_PAUSE_HOURS` |
| Proposals ranked by mentions / windows | R4.4 | ✅ | One-window names pre-deselected unless a narrator (eval-plan E5) |
| Character aliases (`characters.aliases`) | R1.4, R4.5 | ✅ | Proposed by extraction, editable; honoured by fidelity (`Canon.principals`, `canon_terms`), commit merging and scene casting |
| Paragraph normalisation for every format | — | ✅ | HTML block elements, DOCX paragraphs, PDF line-joining → blank-line paragraphs; `Source.format` recorded as an eval covariate |
| Narrator voice attribution | — | ✅ | First-person sections headed "X's Journal" / "Letter, X to Y" (or a lone narrator) are indexed as X's voice |

## 3. Generation

| Feature | Req | Status | Notes |
|---|---|---|---|
| Whole canon reaches the model, untruncated | R1.1 | ✅ | Every generator assembles `Canon.full_block()`; pinned by unit tests and the stub e2e (SPEC-01, -02, -06) |
| Staged outline (skeleton → chapters → beats) | R1.2 | ✅ | `app/planning/staged_outline.py`, checkpointed per stage (SPEC-08) |
| Premise restatement before structuring | R1.3 | 🚧 | Restated at S1; showing it to the author for confirmation is SPEC-10 |
| Beats batched by act, not per chapter | — | ✅ | One call per act |
| Hard gate: principal-cast recall | R1.4 | ✅ | `app/planning/fidelity.py`; aliases honoured (SPEC-09) |
| Soft warning: unknown proper nouns | R1.5 | ✅ | Plan `warnings`, shown in the Outline tab |
| Never hard-fail an expensive job on a heuristic | R1.6 | ✅ | One repair, then save with warnings |
| Outline runs as a job with progress | — | ✅ | Staged and single-call are both jobs; progress by stage (SPEC-07/08) |
| Single-call outline | — | ✅ | Retained for beat sheets, the offline eval, and degraded mode |
| Beat sheet (Save the Cat) | — | ✅ | |
| Promote outline node → chapter | — | ✅ | |
| Scene prose generation | — | ✅ | Currently canon-blind: `characters` is a list of name *strings* |
| Continuity checking | — | ✅ | Ships an **empty cast** today — same dead `book_id` query as the outline |
| Plot threads + events | — | ✅ | |
| Export | — | ✅ | |

## 4. Ensemble (multi-agent scenes)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Narrator / character / editor loop | — | ✅ | `app/orchestration/ensemble.py`; checkpointed per agent call (SPEC-08) |
| Character agents scoped to their own bio + brief + chunks | — | ✅ | `build_agent_context` (SPEC-14) |
| Per-character proposals | — | ✅ | |
| Collapsed cross-character review | — | ✅ | One editor call per round |
| Characters narrate **actions** | R6.1 | ✅ | `actions` required, non-empty |
| Action grounding retrieved separately | R6.2 | ✅ | `<voice>` + `<physicality>` per agent, untyped fallback (SPEC-14) |
| Editor verifies every present character acted | R6.3 | ✅ | Missing actors become blocking objections in code |
| Measured convergence (explicit `satisfied` booleans) | — | ✅ | |
| Anti-oscillation | — | ✅ | |
| Opt-in per scene; prose mode stays default | R7.4 | ✅ | Mode choice in the scene modal; the response reports the mode that ran (SPEC-12) |
| Concurrent ensemble jobs | — | ❌ | Worker runs one job at a time; a long ensemble blocks others. Accepted at this scale |

## 5. Versioning

| Feature | Req | Status | Notes |
|---|---|---|---|
| `entity_versions` (plan, character, canon entry, style, source, chapter, synopsis, scene) | R4.1 | ✅ | Sources and chapters added in SPEC-13 |
| Regeneration appends, never overwrites | R4.2 | ✅ | |
| Forward-only restore | R4.3 | ✅ | |
| Version history + restore UI | — | ✅ | `VersionHistory`; deleted entities in the Trash tab (SPEC-13) |
| `scene_revisions` unified into `entity_versions` | — | ❌ | Different shape and access pattern; two mechanisms is deliberate (ADR-002 §5) |
| Voice-chunk versioning | — | ❌ | Derived; re-embed is idempotent. Chunk text edits are destructive — accepted |
| Version pruning | — | ❌ | None initially ⇒ **the ensemble must never snapshot per agent turn**, only the final scene |

## 6. Retrieval & metadata

| Feature | Req | Status | Notes |
|---|---|---|---|
| Voice chunks embedded per character | — | ✅ | fastembed/ONNX, 384-dim, pgvector HNSW cosine |
| Chunks carry `book_id`; retrieval filters by book | R3.3 | ✅ | `index_chunks` requires it; retrieval scopes by user and book |
| Chunk browser / editor with re-embed | R5.2 | ✅ | Canon tab → character → **Voice** (`VoicePanel`): paged list (type, source, words) with type filter, inline edit (re-embeds via PATCH), delete, and "Add samples" (one per paragraph). API pages with `skip/limit` and filters by `source_id`/`chunk_type` (SPEC-11) |
| Retrieval inspector (chunks + scores) | R5.3 | ✅ | Voice → "Test retrieval": ranked hits with scores and the `RAG_SCORE_THRESHOLD` line drawn; "Test a line" shows the samples that grounded it (SPEC-11) |
| Grounding provenance per generated scene | R5.3 | ✅ | Prose and ensemble store `{beat: [{character_id, chunk_id, score}]}` on `Scene.evaluation_scores["grounding"]`; `GET /books/scenes/{id}/grounding` resolves to current text (deleted chunks marked); "Why does it sound like this?" in the scene list and editor (SPEC-11) |
| Structured character field editors | R5.4 | ✅ | Key-value editor (PR #30) |
| `character_chunks` mirror table | — | ❌ | Dropped (SPEC-11): no longer written, ORM model removed, migration `spec11_drop_character_chunks` drops the table; `voice_chunks` is the one store |
| Retrieval scoped by user | — | ✅ | Opt-in per call; `dialogue.py` omits it while `context.py` passes it — half-wired |

## 7. Text handling

| Feature | Req | Status | Notes |
|---|---|---|---|
| Prose passed through structure-preserving | R2.1 | ✅ | `app/core/llm_text.py::clean_for_llm` |
| Overflow raises, never truncates | R2.2 | ✅ | `TextTooLargeError`; no-slice checks in unit tests for every generator |
| Structural injection fence (spotlighting) | R2.3 | ✅ | `as_quoted_block` + `STORY_MATERIAL_NOTICE` on every generator (SPEC-01/02) |
| Chat-template control tokens escaped, not deleted | R2.1 | ✅ | |
| Lexical phrase blocklist | R2.3 | ❌ | **Rejected on the merits**: trivially bypassed ⇒ ~zero security, and it ate a book |
| `sanitize_for_llm` deleted | — | ✅ | Zero callers; removed |
| Model output treated as untrusted at point of use | R2.4 | ✅ | Strict validators (`validate_outline_nodes`); never eval'd/shelled/SQL'd |
| Capability containment (why permissiveness is safe) | — | ✅ | Text-only generation, no tool use, scoped to one user's book. **Re-open ADR-002 §4 if this changes** |
| Filename / path / upload / redirect validation | — | ✅ | Legitimate — real sinks where those controls are correct; untouched |

## 8. Tiering & budget

| Feature | Req | Status | Notes |
|---|---|---|---|
| Provider-fungible LLM backend | — | ✅ | ADR-001 §2 — Gemini / Groq / xAI / OpenAI. Paid capacity needs **no new code** |
| Per-provider RPM pacing | — | ✅ | Gemini free `max_rpm=8` |
| Per-user daily token budget | — | ✅ | `USER_DAILY_TOKEN_LIMIT=200_000` (our own number) |
| Token/cost metrics + `api_usage` accounting | — | ✅ | Already per-user and per-purpose — the paid-tier foundation |
| `Tier` capability object | R7.6 | ✅ | Owns pacing, token cap, RPD tracking and gates (SPEC-03) |
| Quota exhaustion **pauses**, never fails | R7.2 | ✅ | Re-queue via `jobs.available_at`; mirrored on every domain row with a resume time; bounded by `JOB_MAX_PAUSE_HOURS`; cancellable (SPEC-07) |
| Incremental resume (no repeated model call) | R7.2 | ✅ | Job-scoped checkpoints: prose beats, ensemble agent calls, staged-outline stages/acts, extraction windows; canon drift restarts (SPEC-08) |
| Preflight multi-call jobs against budget | R7.3 | ✅ | Scenes, outline, continuity, extraction — one estimator (SPEC-12) |
| Graceful degradation | R7.4 | ✅ | Staged → single-call; ensemble → prose, reported in the response (SPEC-12) |
| Show cost before an expensive action | R7.5 | ✅ | `EstimateHint` + `POST /estimate`; Usage page (SPEC-12) |
| Paid graduation = one config flip | R7.6 | ✅ | `LLM_TIER=paid` (SPEC-03) |
| Monthly cost ceiling | — | ✅ | Enforced deployment-wide over 30 days (SPEC-03) |

## 9. Evals

| Feature | Req | Status | Notes |
|---|---|---|---|
| Corpus with a synopsis past the historical cap | — | ✅ | `evals/corpora/cel` |
| Cast first appearing past char 4,000 | — | ✅ | `cel`; asserted in `test_fidelity_gate.py` |
| `cast_fidelity` grader | — | ✅ | `evals/graders/fidelity.py` |
| Evals pass a populated bible | — | ✅ | Outline and prose steps seed the corpus cast (SPEC-06) |
| Outline score gated on fidelity | — | ✅ | `cast_fidelity × judge` (API) / `× beat_recall` (offline) |
| CI gate `cast_fidelity ≥ 0.9` | — | ✅ | Every PR (stub e2e job) and the live `evals.yml` run (SPEC-06) |
| "Elara outline" regression fixture | — | ✅ | `tests/unit/test_fidelity_gate.py`, zero API cost |
| Judge rubric mentions fidelity | — | ✅ | |
| Attribution / continuity / extraction / retrieval graders | — | ✅ | |

## 10. Platform

| Feature | Status | Notes |
|---|---|---|
| Invite-gated multi-user auth, rotating refresh tokens | ✅ | |
| Rate limits per real client IP (trusted `CF-Connecting-IP`) and per user | ✅ | SPEC-05 / F7. Was keyed on the tunnel, so every limit was global |
| Per-email failed-login limit; polling and LLM route classes; `Retry-After` | ✅ | SPEC-05. UI polls back off on 429 |
| Background jobs (lock, retry, dead-letter) | ✅ | `available_at` already exists — the pause/resume mechanism |
| Jobs API: `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | ✅ | SPEC-07. Status, resume time, progress `{done,total}`; every enqueueing endpoint returns `job_id`; dedupe keys + `Idempotency-Key` on scene generation; one `JobStatusBadge` across scenes, outline, extraction, continuity |
| One container, one Postgres (+pgvector) | ✅ | ADR-001 |
| Explicit-DDL migration baseline | ✅ | Frozen DDL in `0001_book_as_root_baseline` (the sqlite branch is a test-only safety net) |
| Book-scoped UI (characters + sources + scenes under the book) | ✅ | Scenes are book-rooted and listed per book (SPEC-04); deleting a chapter asks where its scenes go. `/scenes/` stays only as an authenticated list of scenes reachable through the user's books |
| `BookProvider` in the frontend | ⬜ | Minimal — `?id=` is prop-drilled today; resist a grand refactor |
| Prometheus metrics, health, `/__version` SHA gate | ✅ | |
