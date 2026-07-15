# Polyphony — Feature set

The catalogue behind [BRD.md](BRD.md). Requirement IDs (`R1.1`…) refer to BRD §5; decision numbers
refer to [ADR-002](ADR-002-book-as-root.md). Status reflects the book-as-root effort, not the
product's whole history.

**Legend** — ✅ shipped · 🚧 in flight · ⬜ planned · ❌ deliberately not doing

---

## 1. Canon (the book's authored truth)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Book: title, author, genre, synopsis, status | — | ✅ | `synopsis` is unbounded `Text`; needs an editor sized for real prose (R5.1) |
| Characters scoped to exactly one book | R3.1 | ⬜ | `book_id` → `NOT NULL CASCADE`. Today the column exists and **no code writes it** |
| Character names unique within a book | R3.2 | ⬜ | Today manual characters have **no uniqueness at all** (index keyed on NULL `manuscript_id`) |
| Character bible fields (role, goals, arc, relationships, traits, voice, notes) | — | ✅ | Exist on the model; `goals`/`arc`/`relationships`/`notes` are **dropped** by the outline's bible renderer |
| Canon entries (categorised worldbuilding) | — | ⬜ | `category`: world / location / faction / item / concept / org |
| Style guide (POV, tense, tone, comps, sample prose) | — | ⬜ | One per book |
| Free-form notes | — | ⬜ | No new entity — a `Source` with `kind='paste'` (ADR-002 §2) |
| Canon fully viewable + editable | R5.1 | ⬜ | |
| Cross-book character reuse | — | ❌ | Explicit copy, not a shared entity (BRD §2.2) |

## 2. Sources (raw input)

| Feature | Req | Status | Notes |
|---|---|---|---|
| Upload a document (docx/pdf/txt/md) | — | ✅ | Currently `Manuscript`, user-scoped |
| `Manuscript` → `Source`, scoped to a book | — | ⬜ | ADR-002 §2. `/manuscripts` route deleted; you upload **into a book** |
| Paste raw text as a source | — | ⬜ | `kind='paste'` |
| Deleting a source never deletes the cast | — | ⬜ | `characters.source_id` → `SET NULL` **and** drop the ORM cascade — both halves or neither |
| Per-user content-hash dedupe | — | ✅ | Deliberately per-user: a global unique leaks a cross-tenant existence oracle (migration `0003`) |
| Extraction → **proposed** canon, reviewed per item | R4.4 | ⬜ | Proposes; never commits silently |

## 3. Generation

| Feature | Req | Status | Notes |
|---|---|---|---|
| Whole canon reaches the model, untruncated | R1.1 | ⬜ | **The bug.** Today: 2,000 of 20,001 chars + an empty bible |
| Staged outline (skeleton → chapters → beats) | R1.2 | ⬜ | Whole canon in context at every stage |
| Premise restatement before structuring | R1.3 | ⬜ | Catches a misread ("the CEL") at stage 1 for one call |
| Beats batched by act, not per chapter | — | ⬜ | 12 chapters = 12 calls = 90 s of pure pacing at 8 RPM; ~4 acts ≈ 4 calls |
| Hard gate: principal-cast recall | R1.4 | ⬜ | Exact match, deterministic, no false positives. The Elara detector |
| Soft warning: unknown proper nouns | R1.5 | ⬜ | Warns only — a good outline may invent an innkeeper |
| Never hard-fail an expensive job on a heuristic | R1.6 | ⬜ | Always save with warnings attached |
| Outline runs as a job with progress | — | ⬜ | ~6 calls × RPM pacing will time out behind any proxy |
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
| Narrator / character / editor loop | — | ⬜ | |
| Character agents scoped to their own bio + brief + chunks | — | ⬜ | ~800 tok. Cheaper **and** truer: a character does not know the plot |
| Per-character proposals | — | ⬜ | Kept independent — distinct voice is the product's namesake |
| Collapsed cross-character review | — | ⬜ | Objections need consistency across characters; one call |
| Characters narrate **actions** | R6.1 | ⬜ | `actions` required, non-empty. Today `_generate_action` derives action *from* dialogue — backwards |
| Action grounding retrieved separately | R6.2 | ⬜ | Else dialogue crowds it out |
| Editor verifies every present character acted | R6.3 | ⬜ | |
| Measured convergence (explicit `satisfied` booleans) | — | ⬜ | Never inferred |
| Anti-oscillation | — | ⬜ | If round 2's objections ≥ round 1's, keep round 1's prose |
| Opt-in per scene; prose mode stays default | R7.4 | ⬜ | |
| Concurrent ensemble jobs | — | ❌ | Worker runs one job at a time; a long ensemble blocks others. Accepted at this scale |

## 5. Versioning

| Feature | Req | Status | Notes |
|---|---|---|---|
| `entity_versions` (plan, character, canon entry, style, source, synopsis) | R4.1 | ⬜ | One generic table; hard FK on `book_id`, soft entity pointer |
| Regeneration appends, never overwrites | R4.2 | ⬜ | Today `plan.content = nodes` **destroys** the prior outline and any hand edits |
| Forward-only restore | R4.3 | ⬜ | Restoring v2 appends v4 carrying v2's content |
| Version history + restore UI | — | ⬜ | |
| `scene_revisions` unified into `entity_versions` | — | ❌ | Different shape and access pattern; two mechanisms is deliberate (ADR-002 §5) |
| Voice-chunk versioning | — | ❌ | Derived; re-embed is idempotent. Chunk text edits are destructive — accepted |
| Version pruning | — | ❌ | None initially ⇒ **the ensemble must never snapshot per agent turn**, only the final scene |

## 6. Retrieval & metadata

| Feature | Req | Status | Notes |
|---|---|---|---|
| Voice chunks embedded per character | — | ✅ | fastembed/ONNX, 384-dim, pgvector HNSW cosine |
| Chunks carry `book_id`; retrieval filters by book | R3.3 | ⬜ | Column exists, **never passed**; `retrieve_similar` has no `book_id` param at all |
| Chunk browser / editor with re-embed | R5.2 | ⬜ | Store is **write-only** today: the only fix for one bad sample is deleting the character |
| Retrieval inspector (chunks + scores) | R5.3 | ⬜ | Score is already computed at `store.py:113-130` and **discarded** |
| Structured character field editors | R5.4 | ⬜ | `personality_traits` / `voice_characteristics` / `relationships` have **no UI at all** |
| `character_chunks` mirror table | — | ⬜ | Written by both ingest paths, **never read** — reconcile or drop |
| Retrieval scoped by user | — | ✅ | Opt-in per call; `dialogue.py` omits it while `context.py` passes it — half-wired |

## 7. Text handling

| Feature | Req | Status | Notes |
|---|---|---|---|
| First-party prose passed unaltered | R2.1 | ⬜ | Today: HTML-escaped (`It&#x27;s`), markdown `---` rewritten to `[FILTERED]-` |
| Overflow raises, never truncates | R2.2 | ⬜ | A silent cap is what kept this bug invisible for its entire life |
| Structural injection fence | R2.3 | ⬜ | Replaces lexical scrubbing; alters zero characters of prose |
| `sanitize_for_llm` deleted | — | ⬜ | All nine call sites are first-party ⇒ zero callers ⇒ delete. Don't leave a loaded gun |
| Filename / path / upload / redirect validation | — | ✅ | Legitimate; untouched |

## 8. Tiering & budget

| Feature | Req | Status | Notes |
|---|---|---|---|
| Provider-fungible LLM backend | — | ✅ | ADR-001 §2 — Gemini / Groq / xAI / OpenAI. Paid capacity needs **no new code** |
| Per-provider RPM pacing | — | ✅ | Gemini free `max_rpm=8` |
| Per-user daily token budget | — | ✅ | `USER_DAILY_TOKEN_LIMIT=200_000` (our own number) |
| Token/cost metrics + `api_usage` accounting | — | ✅ | Already per-user and per-purpose — the paid-tier foundation |
| `Tier` capability object | R7.6 | ⬜ | One object read by budget/client/planning/ensemble |
| Quota exhaustion **pauses**, never fails | R7.2 | ⬜ | Re-queue via the existing `jobs.available_at`; honour server retry interval |
| Preflight multi-call jobs against budget | R7.3 | ⬜ | Refuse to start rather than half-write |
| Graceful degradation | R7.4 | ⬜ | Staged → single-call; ensemble → prose |
| Show cost before an expensive action | R7.5 | ⬜ | |
| Paid graduation = one config flip | R7.6 | ⬜ | Set key → `LLM_TIER=paid` → set ceiling |
| Monthly cost ceiling | — | ⬜ | `MONTHLY_COST_CEILING_USD` |

## 9. Evals

| Feature | Req | Status | Notes |
|---|---|---|---|
| Corpus with a synopsis past the historical cap | — | ⬜ | **The truncation branch has never executed in an eval run** (451/607 chars vs a 2,000 cap) |
| Cast first appearing past char 4,000 | — | ⬜ | The only way to test "characters first appear at char 4,483" |
| `cast_fidelity` grader | — | ⬜ | `principal_recall × (1 − unknown_rate)`; reuses the existing per-corpus `aliases.json` |
| Evals pass a populated bible | — | ⬜ | Today `offline_outline` passes none and `step_outline` creates a book with no characters |
| Outline score gated on fidelity | — | ⬜ | `cast_fidelity * beat_recall` — a beautiful outline about the wrong story scores 0 |
| CI gate `cast_fidelity ≥ 0.9` | — | ⬜ | Without a gate, a grader is decoration |
| "Elara outline" regression fixture | — | ⬜ | The actual incident, asserted at **zero API cost** |
| Judge rubric mentions fidelity | — | ⬜ | Secondary — the deterministic grader is free and trustworthy |
| Attribution / continuity / extraction / retrieval graders | — | ✅ | |

## 10. Platform

| Feature | Status | Notes |
|---|---|---|
| Invite-gated multi-user auth, rotating refresh tokens | ✅ | |
| Background jobs (lock, retry, dead-letter) | ✅ | `available_at` already exists — the pause/resume mechanism |
| One container, one Postgres (+pgvector) | ✅ | ADR-001 |
| Explicit-DDL migration baseline | ⬜ | `0001`'s `create_all` **drifts with the ORM**; the trap already bit `0006` |
| Book-scoped UI (characters + sources under the book) | ⬜ | Today both are top-level routes with zero book awareness |
| `BookProvider` in the frontend | ⬜ | Minimal — `?id=` is prop-drilled today; resist a grand refactor |
| Prometheus metrics, health, `/__version` SHA gate | ✅ | |
