# ADR-002: Book as the root of every concept; Canon; free-first tiering

**Status**: Accepted
**Date**: 2026-07-14
**Context**: Polyphony produced a 12-chapter outline for a 20,001-character synopsis that starred
an invented protagonist ("Elara"), misread the in-world term "the CEL" (Chief Executive Lich) as
a company name, and dropped the author's entire cast and storyboard. Reproduction showed the model
received **2,000 of 20,001 synopsis characters (6.5%) and an empty character bible**. The defects
were not in the prompt — which already begged for fidelity — but in the data model and the text
pipeline. See [BRD](BRD.md) §1 for the full diagnosis and [docs/examples](examples) for the
originating incident.

The precipitating facts: `Character.book_id` existed but was **written by no code path**, so the
outline's bible query always returned zero rows; `sanitize_for_llm` truncated the synopsis to a
general-purpose 2,000-char default, HTML-escaped it, and rewrote its markdown; and the evals'
corpus synopses (451 and 607 chars) were 4× under the cap, so the truncation branch had **never
executed in a single eval run**.

## Decisions

### 1. Book is the root of every concept
`characters.book_id` becomes `NOT NULL, ON DELETE CASCADE`, with a real `Book.characters`
relationship. Every canon concept — characters, canon entries, style, sources, plans — is
reachable only through a book. There is no user-level bible and no NULL-scoped entity.

Consequences: the `(manuscript_id, name)` unique index moves to `(book_id, name)` — which finally
gives manual characters *any* name uniqueness at all (the old index keyed on a NULL
`manuscript_id`, and NULLs are distinct in Postgres). Cast-fidelity checking depends on this.
`voice_chunks.book_id` becomes populated and filterable. `characters/context.py`'s
`| book_id.is_(None)` branch — which made the feature work *by accident* — is deleted.

The FK direction is load-bearing: `NOT NULL` + `ON DELETE SET NULL` would violate the constraint
and 500 the book-delete endpoint. It must be `CASCADE`.

### 2. `Manuscript` is rolled into `Source`
A manuscript and a pile of pasted notes are the same thing: raw material that arrived somehow.
They become one entity, `Source` (`kind`: `upload` | `paste`), scoped `book_id NOT NULL`. The
`/manuscripts` concept and route are deleted; **you upload into a book**.

This is what collapses the two parallel trees (`user → manuscripts → characters` and
`user → books → …`) rather than patching the seam between them — the same structural reasoning as
ADR-001 §1, where inter-service seams were deleted rather than hardened. It also satisfies the
free-form-notes requirement with no new entity.

`characters.source_id` is **provenance only, `ON DELETE SET NULL`**, and the ORM's
`cascade="all, delete-orphan"` on sources→characters is removed. **Both halves must change
together** or SQLAlchemy still deletes the cast in Python. Deleting a source file must never
delete the canon: the characters *are* the book now; the file was merely how they arrived.

Alternative rejected: keeping `Manuscript` user-scoped with a nullable `book_id`. It preserves
the ambiguity that produced this bug, and leaves Book as root in name only.

### 3. "Canon", not "Story Bible"
The umbrella term for a book's authored truth is **Canon** (synopsis + characters + canon entries
+ style). Deliberately avoids vocabulary owned by other products or by our own sibling Greenlight
tools: "Story Bible"/"Braindump" (Sudowrite), "Codex"/"Lore" (NovelCrafter), "Muse" (ours).
"Canon" is generic craft vocabulary, is literally a polyphonic musical form — on-brand for this
product — and is already the word the fidelity design reasons in (`canon_terms`). Worldbuilding
becomes **canon entries** with a `category`, so extending needs data, not schema.

### 4. First-party prose is not sanitized
`sanitize_for_llm` conflated three unrelated jobs — injection defence against untrusted text,
length control, and XSS escaping for HTML display — and applied all three to the author's own
manuscript. All three are wrong there. The LLM is not a browser (React already escapes on
render), so `&#x27;` only corrupts model input; the length control silently destroyed 93.5% of a
book; and the injection filter rewrites `---`, `system:`, and other legitimate prose.

Replacement: `clean_for_llm` in a new `app/core/llm_text.py` — control-character stripping and
newline normalisation only. **Overflow raises rather than truncates**: a silent cap is exactly
what kept this defect invisible for its whole life, so the structural fix is to make the failure
loud. Injection defence becomes **structural, not lexical**: author content is fenced in a
delimited block the model is told to treat as story material, never instructions — altering not
one character of the prose.

All nine `sanitize_for_llm` call sites are first-party, so all migrate and the function is
**deleted** rather than left loaded. `sanitization.py` keeps its legitimate consumers
(`sanitize_filename`, `sanitize_file_path`, `validate_file_upload`, `is_safe_redirect_url`, …).
This is sound only because Polyphony is single-author (BRD §2.2); if sharing ever ships, the
fence — not a regex that eats em-dashes — is the control that scales.

### 5. One generic `entity_versions` table; `scene_revisions` stays
Five-plus types (plan, character, canon entry, style guide, source, synopsis) need identical
snapshot / list / restore, and every query is `(entity_type, entity_id) → version_no DESC` with
no cross-type joins. Per-entity mirrors would mean five of everything, plus a sixth each time a
canon type is added.

The usual objection to polymorphism is FK integrity — and **decision 1 dissolves it**: because
book is the root, the table carries a *hard* FK on `book_id` (real cascade) while
`(entity_type, entity_id)` stays a soft pointer. Full snapshots, never diffs (canon is kilobytes;
diffs make restore fragile).

**Invariant, deliberately opposite to `SceneRevision`**: `entity_versions` is an append-only log
of every state the entity has ever held **including the current one** — `max(version_no)` always
equals the live row. `SceneRevision` instead stores history *excluding* head. Restore is
forward-only: restoring v2 appends v4 carrying v2's content. This makes "regeneration never
clobbers" literally true *and inspectable* — old generation is v2, new is v3, both browsable.

`scene_revisions` is **not** migrated: it is live, has an API and frontend, and has a genuinely
different shape (large `Text` + `word_count`) and access pattern. Two mechanisms is accepted
deliberately; a future adapter behind one repository API can fold them if the duplication bites.

### 6. Evals must be able to see fidelity failures
A grader that cannot fail is decoration. The corpus gains a synthetic book whose synopsis exceeds
the historical cap and whose cast first appears past char 4,000 — the only way to exercise a code
path that has never run under test. A deterministic `cast_fidelity` grader (principal-cast recall
× unknown-noun rate, reusing the existing per-corpus `aliases.json` as the allowlist) gates CI.
The outline score becomes `cast_fidelity * beat_recall`, so a beautiful outline about the wrong
story scores zero.

The hard gate is **recall of the known cast**, not precision against unknown nouns: a good outline
legitimately invents an innkeeper, and "Elara" was a *recall* failure — the real protagonist was
absent. Recall is exact-match, deterministic, and free of false positives; unknown nouns only warn.

### 7. Free-tier-first with pause-and-resume; paid is a config flip
Quota exhaustion (429 / `RESOURCE_EXHAUSTED`) is classified distinctly from transient errors and
**pauses** a job — re-queuing it via the existing `jobs.available_at` column for when quota
returns, honouring the server's suggested interval — instead of exhausting retries, tripping the
circuit breaker, and losing the work. Capability lives in one `Tier` object read by budget,
client, planning, and ensemble, so graduation is one setting, not scattered conditionals.

This deliberately builds nothing new for paid capacity: ADR-001 §2's provider-fungible registry
already covers paid Gemini / Groq / xAI / OpenAI via `LLM_PROVIDER` + a key, and `api_usage` plus
the existing Prometheus token/cost metrics already provide per-user, per-purpose accounting.

### 8. Squash the migration baseline
`0001_consolidated_baseline` calls `Base.metadata.create_all`, so it **drifts with the ORM**: a
fresh database receives whatever the models currently define, and any additive migration then runs
against already-correct state. This trap has already bitten once (`"make 0006 constraint adds
idempotent (fresh-DB baseline overlap)"`). Because live data is disposable, the chain squashes to
a single explicit-DDL baseline carrying the target shape — killing the drift permanently and
letting the foreign keys be *written correctly from the start* instead of backfilled and flipped.

If data ever must survive, the alternative is an additive migration in `0005_tenant_ownership`'s
backfill-then-enforce shape: auto-create a book per source, sweep orphans into a per-user
"Unsorted" book (**renaming duplicates — never deleting author-written rows**), then enforce
`NOT NULL` and flip the FKs, guarding every statement for the fresh-DB overlap.

## Alternatives considered

- **Fix the prompt** — already tried (`f22527b`, "develop the premise faithfully"). It cannot work: the prompt was begging the model to use characters the pipeline had already deleted, and it was tuned against an eval that scores structure, not fidelity.
- **Write `book_id` on new characters only, skipping the backfill** — rejected: existing characters stay NULL, so the author's actual book still gets an empty bible and their pain is not fixed.
- **Keep characters user-scoped with an optional shared bible** (`NULL` = available to every book) — rejected: it preserves exactly the ambiguity that caused this defect, and `context.py`'s accidental reliance on the NULL branch shows how that ambiguity rots into behaviour nobody designed.
- **Many-to-many characters across books** (series support) — rejected as out of scope (BRD §2.2); no current code expects it, and reuse-by-copy is sufficient.
- **Per-entity `*_revisions` tables** mirroring `SceneRevision` — rejected: five near-identical tables and a sixth per new canon type, for query patterns that never join across types.
- **spaCy / NER for cast fidelity** — rejected: a new heavyweight dependency on a 6 GB box, to produce a *fuzzier* signal than exact-matching a known cast list.
