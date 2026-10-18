# Polyphony — Feature set

The catalogue behind [BRD.md](BRD.md). Requirement IDs (`R1.1`…) refer to BRD §5; decision numbers
refer to [ADR-002](ADR-002-book-as-root.md). Status reflects the book-as-root effort, not the
product's whole history.

**Status as of 2026-10-16** (reconciled against the code; see [docs/specs](specs/README.md)).

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
| Upload a document (docx/pdf/txt/html) | — | ✅ | The dialog reads `GET /sources/config` (accepted types, max size); an unsupported type is a 415 before the body is read, and `.doc` gets "save as .docx". Markdown is not supported: the parser has no `.md` path (SPEC-21) |
| `Manuscript` → `Source`, scoped to a book | — | ✅ | ADR-002 §2; you upload **into a book** (PR #19, #29) |
| Paste raw text as a source | — | ✅ | `kind='paste'` (PR #23) |
| Deleting a source never deletes the cast | — | ✅ | `characters.source_id ON DELETE SET NULL` and no ORM cascade; deletes are soft and restorable (SPEC-13) |
| Per-user content-hash dedupe | — | ✅ | Deliberately per-user: a global unique leaks a cross-tenant existence oracle (migration `0003`) |
| Extraction → **proposed** canon, reviewed per item | R4.4 | ✅ | Windowed map-reduce at manuscript scale (SPEC-09); never pre-approves over the author's synopsis or style |
| Extraction at manuscript scale (map-reduce) | R4.4 | ✅ | SPEC-09: ~12k-token windows, alias-aware merge, batched full entries, synopsis last; truncated window retried smaller, never fails the run; per-window checkpoint. Reduce writes each character from up to 8 evidence quotes (≤ 3 per window from the map), up to 6 of their own attributed lines, and the source itself when it is one window (SPEC-26). A quoteless character gets "no verbatim quote in part N" as evidence; narrators keep "narrates part N" (SPEC-64) |
| Extraction cost preflight | R4.4, R7.3, R7.5 | ✅ | Estimate returned on enqueue; refused with "first N chapters" when it cannot finish within `JOB_MAX_PAUSE_HOURS` |
| Proposals ranked by mentions / windows | R4.4 | ✅ | Ranked by parts seen in, then name mentions (given name / surname counted when unambiguous; pronouns not counted) (SPEC-26). One-window names pre-deselected unless a narrator (eval-plan E5) |
| Character aliases (`characters.aliases`) | R1.4, R4.5 | ✅ | Proposed by extraction, editable; honoured by fidelity (`Canon.principals`, `canon_terms`), commit merging and scene casting |
| Paragraph normalisation for every format | — | ✅ | HTML block elements, DOCX paragraphs, PDF line-joining → blank-line paragraphs; verse keeps its line breaks and indentation; `Source.format` recorded as an eval covariate. Scanned PDFs (no text layer) are refused; OCR is out of scope (SPEC-09) |
| Narrator voice attribution | — | ✅ | First-person sections headed "X's Journal" / "Letter, X to Y", a narrator who names themselves once ("my name is X", "I, X,"), or a lone narrator-role character are indexed as X's voice (heuristic, no LLM calls) |

## 3. Generation

| Feature | Req | Status | Notes |
|---|---|---|---|
| Whole canon reaches the model, untruncated | R1.1 | ✅ | Every generator assembles `Canon.full_block()`; pinned by unit tests and the stub e2e (SPEC-01, -02, -06) |
| Staged outline (skeleton → chapters → beats) | R1.2 | ✅ | `app/planning/staged_outline.py`, checkpointed per stage (SPEC-08) |
| Outline beats carry their own cast, setting, tone and named secondaries | R1.5a | ✅ | `app/planning/staged_outline.py`, `outline.py::validate_outline_nodes` (SPEC-24) |
| Named secondaries allowed in the outline; suggestions to add them to the cast | R1.5a | ✅ | `app/planning/secondaries.py` (SPEC-24); `suggested_characters` on `GET /plans` and both promotes (SPEC-23); "Add <name> to cast" chips in the Outline tab (SPEC-27), which store the naming sentence's introduction, not the whole sentence (SPEC-50) |
| Planner treats the cast as available; no blank setting / neutral tone in prompts | R1.1c | ✅ | `app/orchestration/workflow.py` (SPEC-24) |
| POV line with mode + character; last beat ends in POV | R1.1c | ✅ | `app/orchestration/pov.py::resolve_pov`, `pov_instruction`; prose mode only, the ensemble builds its own prompts (SPEC-24). Wave 3: the beat planner (`plan_pov_instruction`, also the ensemble planner) and revise carry the same POV paragraph; a one-head mode has the POV character learn a name before it is used, and omniscient and objective are not held to one head (SPEC-24 wave 3). plan_beats prints `DESCRIBE_NOT_QUOTE` after the POV line (SPEC-61) |
| Story position in scene prompts | R1.1d | ✅ | The planner, beat and revise prompts say "This scene is in chapter N of M" right after `<chapter>`, and that nothing after this chapter has happened yet; a trashed chapter gives no position (`app/orchestration/workflow.py::story_position_line`, `prose.py::scene_chapter`) (SPEC-30) |
| Named secondaries carry their own voice | R1.5b | ✅ | Named secondary characters speak in the voice the author gave them in the sources: a `<secondary>` block with the sentence that introduces each one and up to three of their own lines (or a note to keep them brief), in the planner, every beat prompt and revise (`app/planning/secondaries.py::scene_secondaries`, `render_secondaries`) (SPEC-29) |
| Outline beats stage sees premise, conflict and act goal/turn; JSON, acts from 1 | R1.2a | ✅ | `app/planning/staged_outline.py::_stage_beats_for_act`; the chapters stage embeds acts as JSON too (SPEC-18). Every JSON-only call (outline stages, plan_beats, continuity, extraction) asks the provider for JSON mode (SPEC-51); a scene brief says only what its chapter's POV character can witness, and the outline audit warns otherwise (SPEC-49); both outline stages print one `OUTLINE_POV_LINE` (SPEC-63) |
| Beat prompt sees the whole beat plan (current beat marked, stop line) | R1.1a | ✅ | `app/orchestration/prose.py::write_beat_prose` (SPEC-18) |
| Paragraph-preserving prior tail, never mid-sentence | R1.1a | ✅ | `prose.py::prose_tail`, used by prose, ensemble and `handlers._scene_inputs`; checkpoint hash unchanged, so paused jobs still resume (SPEC-18) |
| Sample prose leads style; house style only when the guide is empty | R1.1a | ✅ | `prose.py::_style_section` (SPEC-18) |
| Deterministic seam-repeat trim between beats | R1.1a | ✅ | `prose.py::trim_seam_repeat`, in prose and ensemble; the trimmed text is what is stored (SPEC-18) |
| Premise restatement before structuring | R1.3 | ✅ | Restated at S1 and shown for confirmation (`awaiting_confirmation`); an edit is authoritative for S2-S4 (SPEC-10) |
| Beats batched by act, not per chapter | R1.2a | ✅ | One call per act; each act's prompt lists the whole outline and the beats earlier acts have (`staged_outline.outline_context`, SPEC-38) |
| Hard gate: principal-cast recall | R1.4 | ✅ | `app/planning/fidelity.py`; aliases honoured (SPEC-09) |
| Soft warning: unknown proper nouns | R1.5 | ✅ | Plan `warnings`, shown in the Outline tab. Reads non-title text only; fragments, sentence openers and common words are not names; the staged outline passes `canon_text=canon_known_text(canon)` so synopsis/bible names are known (SPEC-21). Words written entirely in capitals (signs, stamps) are never reported as possible inventions (SPEC-33). A run before a label token ("Form 9") is not a name; mid-sentence, a function word before one word opens a title ("Our Values") and is kept, before two or more it is trimmed, so a one-word name after a mid-sentence "The" is missed (SPEC-39) |
| Never hard-fail an expensive job on a heuristic | R1.6 | ✅ | One repair, then save with warnings |
| Outline runs as a job with progress | — | ✅ | Staged and single-call are both jobs; progress by stage (SPEC-07/08) |
| Single-call outline | — | ✅ | Retained for beat sheets, the offline eval, and degraded mode |
| Beat sheet (Save the Cat) | — | ✅ | |
| Promote outline node → chapter | — | ✅ | Idempotent on a stable `node_id`; "Promote all"; the chapter carries the node's cast and POV (SPEC-10); the node's children become the chapter's `planned_beats` (data: cast, setting, tone, named secondaries), the title loses its 'Chapter N:' prefix, and the response carries `suggested_characters` (SPEC-23) |
| Structured outline nodes (POV, cast IDs, act, threads) | R1.5 | ✅ | Validated against the book's cast; the fidelity audit reads them strictly (SPEC-10) |
| Planned beats as data | R1.1b | ✅ | `chapters.planned_beats` (migration `0019_w2_planned_beats` backfills from the summary text or the outline node); `PATCH /books/chapters/{id}` `planned_beats` (strict, versioned); the frontend reads `planned_beats`, with the legacy summary lines as fallback (SPEC-25); editable in the chapter and beat editor (SPEC-35); SPEC-23 |
| Scene defaults from a promoted chapter | — | ✅ | The generate-scene modal pre-selects the chapter's cast and POV and offers its planned beats (SPEC-10). The chips, the "Generate all planned scenes" button and the chapter ⋯ menu read `planned_beats`, falling back to the legacy summary lines; each chip shows its setting and tone, and picking one fills the description (and an empty setting) (SPEC-25) |
| Generate all planned scenes of a chapter | — | ✅ | `POST /books/chapters/{id}/scenes/generate-planned`: one scene per planned beat, each from its planned beat (the beat's cast plus the chapter POV, else the chapter cast; the beat's setting and tone; `planned_beat_id` and unresolved `secondary_names` on the request), preflighted for the whole batch (per-beat cast sizes), "llm" rate class, one batch in flight per chapter; the jobs run as a chain so each scene starts from the previous one's tail; beats already written are skipped (SPEC-10 follow-up, SPEC-23). The batch queues the scenes that fit the 24-hour budget, in beat order, and returns `queued`, `deferred` and `reason`; it is refused (429) only when the first scene does not fit, and a later click writes the rest. Cached input tokens are not measured (SPEC-34) |
| Chapter rows and Generate | R1.1b | ✅ | Each row's controls name the chapter's number and title ("Delete chapter 3, Paperwork"). Generate is disabled, with the reason shown, until the book has a chapter and characters. Picking a planned beat shows the cast the scene will be written with (`cast_ids`, resolved by the server on read); the chapter's point-of-view character is always included and cannot be removed (SPEC-32). The row reads "k of n planned scenes written" |
| Chapter and beat editor | R1.1b | ✅ | "Edit chapter N" on the chapter row: title, summary, and each beat's title, summary, cast, setting and tone; add and delete beats (no reorder). One Save sends one `PATCH` with only what changed; a strict-cast 422 marks its beats; a written beat links to its scene. The generate modal and the editor share `CastChips` and its POV lock (`chapterPovLock`) (`PlannedBeatsEditor.tsx`, SPEC-35) |
| Scene titles, book order and editor header | R5.5 | ✅ | A generated scene takes its beat's title, else the first six words of its description (`scene_batch.scene_title`); `PATCH /books/scenes/{id}` renames (not versioned). One book order (`books.book_order`) for the Scenes tab and the editor's Previous / Next; the editor header reads "Ch N · title ›" with the title editable (SPEC-37) |
| Scene prose generation | R1.1 | ✅ | Whole canon and cast by ID (SPEC-01). Per character, voice samples (dialogue + own first-person narration) and movement (action / description / thought, centred on the character) as separate blocks (SPEC-17); the bible is sent once per beat prompt (canon block); per-character blocks carry voice samples and movement (SPEC-26). Both are labelled "for manner only"; a movement sample sharing `RESTATES_MIN = 4` content words with any line of the plot the prompt carries is dropped (`context.restates` over `SceneScope.plot`, SPEC-36, SPEC-40), and a one-head POV line shows only what the POV character sees or hears (SPEC-36). Voice and movement samples from a source's later plot are not shown, a secondary's introduction stops before its later reveals (SPEC-40), and a phrase one character owns is named as theirs where they are absent (SPEC-42). The beat planner sees its chapter's other planned scenes and what the earlier ones planned (SPEC-43). A sample that scripts the beat's line is dropped (SPEC-46); the prose names only people the book knows, and the editor lists the unknown names a scene was generated with (SPEC-48) |
| AI revision of a range / beat / scene | R4.9, R4.10 | ✅ | `POST /books/scenes/{id}/revise` → `revise_scene` job → proposal; `GET …/proposals`, `POST …/revisions/{rid}/accept\|discard`; `estimate` action `revise`; migration `0018_b3_scene_revise` adds `scene_revisions.meta` (SPEC-19). The draft revised is the raw editable text, edge whitespace kept (`app/jobs/revise.py::draft_of`); an ambiguous beat is a 409 "That beat's text appears more than once; select the text instead." Editor UI: SPEC-25. A redo of one beat sees the scene's whole plan and the terms the rest of the chapter refers to, and warns when the redo drops one (SPEC-41); a directed redo changes only what was asked, and each kept term shows its context (SPEC-47); a cut-word request adds `TIGHTEN_CLAUSE` after the return rule (`revise.py::_CUT_WORDS`, SPEC-59) |
| Continuity checking | R1.1 | ✅ | Scene-chunked, quoted findings located to a scene; "Open in editor" opens the scene; a quote that cannot be found is kept and marked "could not locate" (SPEC-31) |
| Plot threads + events | — | ✅ | |
| Export | — | ✅ | Export dialog: format, include synopsis (default off), skip empty chapters (default on), chapter range (1-based, by book position) (SPEC-20). Clean manuscript: "Chapter N: Title" by book position, scene breaks, empty chapters skipped by default, `from_chapter`/`to_chapter` range (SPEC-21) |

## 4. Ensemble (multi-agent scenes)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Narrator / character / editor loop | — | ✅ | `app/orchestration/ensemble.py`; checkpointed per agent call (SPEC-08) |
| Character agents scoped to their own bio + brief + chunks | — | ✅ | `build_agent_context` (SPEC-14) |
| Per-character proposals | — | ✅ | |
| Collapsed cross-character review | — | ✅ | One editor call per round |
| Characters narrate **actions** | R6.1 | ✅ | `actions` required, non-empty |
| Action grounding retrieved separately | R6.2 | ✅ | `<voice>` + `<physicality>` per agent (SPEC-14); `<voice>` is their own words and never falls back, `<physicality>` falls back to untyped retrieval (SPEC-17). Each withheld movement line is recorded with the rule that withheld it in the scene's grounding (SPEC-69); the cast header says "matching their voice samples" only when a sample is shown (SPEC-70) |
| Editor verifies every present character acted | R6.3 | ✅ | Missing actors become blocking objections in code |
| Measured convergence (explicit `satisfied` booleans) | — | ✅ | |
| Anti-oscillation | — | ✅ | |
| Opt-in per scene; prose mode stays default | R7.4 | ✅ | Mode choice in the scene modal; the response reports the mode that ran (SPEC-12) |
| Concurrent ensemble jobs | — | ❌ | Worker runs one job at a time; a long ensemble blocks others. Accepted at this scale |

## 5. Versioning

| Feature | Req | Status | Notes |
|---|---|---|---|
| `entity_versions` (plan, character, canon entry, style, source, chapter, synopsis, scene) | R4.1 | ✅ | Sources and chapters added in SPEC-13; a chapter version carries its promoted cast and POV (restored against the current cast) and its outline node; a chapter version also carries its planned beats (SPEC-23) |
| Regeneration appends, never overwrites | R4.2 | ✅ | |
| Forward-only restore | R4.3 | ✅ | |
| Version history + restore UI | — | ✅ | `VersionHistory`; deleted entities in the Trash tab (SPEC-13); the Outline tab's History for outlines, and a promoted node reads "Promoted · chapter N" (SPEC-38) |
| Scene editor safety net (unsaved-changes guard, local draft autosave + restore) | R5.5 | ✅ | `frontend/lib/useUnsavedChangesGuard.ts`, `frontend/lib/draft-store.ts`; local only, never a `scene_revisions` row (SPEC-16) |
| Revise with AI in the scene editor | R4.10 | ✅ | Revise selection / Redo beat N / Whole scene → side-by-side word diff → Accept / Discard (`components/editor/RevisePanel.tsx`, `DiffView.tsx`). Only the saved draft is revised (offsets in code points, `base_text_hash` guard); Accept keeps the previous draft as one revision and leaves the editor clean; Discard leaves the text byte-identical; a pending proposal is offered again after a reload. The Revisions list excludes `ai_revise` proposals (SPEC-25) |
| `scene_revisions` unified into `entity_versions` | — | ❌ | Different shape and access pattern; two mechanisms is deliberate (ADR-002 §5) |
| Voice-chunk versioning | — | ❌ | Derived; re-embed is idempotent. Chunk text edits are destructive — accepted |
| Version pruning | — | ❌ | None initially ⇒ **the ensemble must never snapshot per agent turn**, only the final scene |

## 6. Retrieval & metadata

| Feature | Req | Status | Notes |
|---|---|---|---|
| Voice chunks embedded per character | — | ✅ | fastembed/ONNX, 384-dim, pgvector HNSW cosine |
| Quote-span speech attribution | R6.4 | ✅ | `app/parsing/character_extractor.py::attribute_speech`: a quote is credited only from an adjacent tag in the same paragraph, an unambiguous action beat or a two-speaker exchange; aliases and cast honoured, an ambiguous name credits nobody. Precision ≥ 0.95 on `tests/fixtures/dialogue_attribution.json` gates CI (SPEC-17) |
| `reindex-voice --book <id>` admin command | R6.4 | ✅ | `python -m app.cli reindex-voice --book <id>`: re-runs voice indexing per source for its cast; manual samples and other sources' chunks untouched; idempotent (SPEC-17) |
| Chunks carry `book_id`; retrieval filters by book | R3.3 | ✅ | `index_chunks` requires it; retrieval scopes by user and book |
| Chunk browser / editor with re-embed | R5.2 | ✅ | Canon tab → character → **Voice** (`VoicePanel`): paged list (type, source, words) with type filter, inline edit (re-embeds via PATCH), delete, and "Add samples" (one per paragraph). API pages with `skip/limit` and filters by `source_id`/`chunk_type` (SPEC-11) |
| Retrieval inspector (chunks + scores) | R5.3 | ✅ | Voice → "Test retrieval": ranked hits with scores and the `RAG_SCORE_THRESHOLD` line drawn (SPEC-11) |
| "Test a line" (test dialogue) in the voice workbench | R6.4 | ✅ | Scene-path samples (own words, 3+ words, movement filtered to the probe's character), printed by the scene's renderer; shows its grounding excerpts and scores. One retrieval (`context.character_samples`), no dialogue cache (SPEC-11, SPEC-17 wave 3) |
| Grounding provenance per generated scene | R5.3 | ✅ | Prose and ensemble store `{beat: [{character_id, chunk_id, score, excerpt}]}` on `Scene.evaluation_scores["grounding"]` (a ~300-char excerpt of each sample as it was at generation); every character in the beat has an entry, with a `reason` when no sample grounded it ("no indexed voice samples", ensemble "context only"); `GET /books/scenes/{id}/grounding` resolves to current text, and a deleted chunk shows its stored excerpt marked "deleted since generation"; "Why does it sound like this?" in the scene list and editor (SPEC-11). The route also lists the scene's stored beats that have no recorded grounding, which the editor's "Redo beat N" reads (SPEC-25) |
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
| Preflight multi-call jobs against budget | R7.3 | ✅ | Scenes, outline, continuity, extraction — one estimator (SPEC-12); a planned batch queues the prefix that fits (`estimates.affordable`, SPEC-34). Cached input tokens: not measured (wave 5) |
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
| Eval harness v2 | — | ✅ | Rubric judge (R1–R6) with a separate judge endpoint (`EVAL_JUDGE_BASE_URL`); prompt-contract grader; seam grader; cel corpus with canon, style guide, voice excerpt and planted contradictions; the prose step runs the product path (SPEC-22) |
| Eval harness v3 | — | ✅ | Scene R1 caps only a planned principal neither named nor the attributed speaker of a quoted line (unnamed speech is a warning); prompt-contract checks for P1–P10 (secondaries, beat fields, beat cast, available-not-exact, blank setting, neutral tone, POV line, last beat in POV, single bible, no doubled label, reduce evidence), `rate: None` when no call was checked; extraction depth and mention order; outline secondaries; cast-from-beat; a `revise` step (RA + R5); `python -m evals.harness.rescore` re-grades a finished run (SPEC-28) |
| Eval harness v4 | — | ✅ | Extraction precision counts the corpus' `secondary_cast`; `principal_mentions_min` ≥ 10 replaces the mention-order check; attribution reports `gap` / `mean_gap` with an in-world probe; the continuity control is graded against the corpus canon; prompt-contract checks `secondary_has_context`, `story_position_line` and `movement_names_in_scene` (with `movement_coverage` ≥ 0.8) (SPEC-29, SPEC-30, SPEC-33) |
| Eval harness v5 | — | ✅ | No fallback scene (`path: "failed"`, never cached); every planned scene judged, up to five; prose reports `queued`, `deferred` and `estimate_over_actual`; per-role voice probes; whole gold utterances; the outline book commits its secondaries as minor; prompt-contract checks `sample_lift` (gated 0 / 0; W3 baseline 2/20 under the wave-4 label) and `beats_see_whole_outline` (header only on a resumed outline) (SPEC-39) |
| Eval harness, wave 9 | — | ✅ | Declared runs are preflighted (step list, relay archive, fresh cache and `answered/`, daily limit ≥ the run's measured minimum) and the harness logs in again on a 401 (SPEC-68); movement credits a block whose eligible lines the product withheld as plan-restating, read from the scene's grounding `withheld` record, with rule-named starved reasons and a per-principal line (SPEC-69); attribution is one symmetric situation with names masked, reported with Wilson intervals (SPEC-67) |

## 10. Platform

| Feature | Status | Notes |
|---|---|---|
| Invite-gated multi-user auth, rotating refresh tokens (a just-rotated token is honoured for 30 s, `REFRESH_REUSE_GRACE_SECONDS`, so an aborted /refresh does not sign the user out; each login is a token family, logout ends the whole family and an ended token is never honoured; migration 0020) | ✅ | |
| Rate limits per real client IP (trusted `CF-Connecting-IP`) and per user | ✅ | SPEC-05 / F7. Was keyed on the tunnel, so every limit was global |
| Per-email failed-login limit; polling and LLM route classes; `Retry-After` | ✅ | SPEC-05. UI polls back off on 429 |
| Background jobs (lock, retry, dead-letter) | ✅ | `available_at` already exists — the pause/resume mechanism |
| Jobs API: `GET /jobs/{id}`, `POST /jobs/{id}/cancel` | ✅ | SPEC-07. Status, resume time, progress `{done,total}`; every enqueueing endpoint returns `job_id`; dedupe keys + `Idempotency-Key` on scene generation; one `JobStatusBadge` across scenes, outline, extraction, continuity |
| One container, one Postgres (+pgvector) | ✅ | ADR-001 |
| Explicit-DDL migration baseline | ✅ | Frozen DDL in `0001_book_as_root_baseline` (the sqlite branch is a test-only safety net) |
| Book-scoped UI (characters + sources + scenes under the book) | ✅ | Scenes are book-rooted and listed per book (SPEC-04); deleting a chapter asks where its scenes go. `/scenes/` stays only as an authenticated list of scenes reachable through the user's books |
| Book page usable at phone width | ✅ | Chapter rows stack title over actions below `sm:`, secondary actions in a "⋯" menu; tab strip shows a scroll affordance and keeps the active tab in view (SPEC-20) |
| Accessibility (B13) | 🚧 | Revision preview is a modal dialog with focus trap, Escape and focus return (SPEC-16); the row "⋯" menu follows the WAI-ARIA menu-button pattern (SPEC-20). Form labels, review labels and an axe gate are not covered yet |
| `BookProvider` in the frontend | ⬜ | Minimal — `?id=` is prop-drilled today; resist a grand refactor |
| Prometheus metrics, health, `/__version` SHA gate | ✅ | |
