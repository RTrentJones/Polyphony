# Polyphony — Business Requirements Document

**Status**: Draft for review
**Date**: 2026-07-14
**Supersedes**: nothing (first BRD)
**Related**: [ADR-001](ADR-001-consolidation.md) (one container, fungible LLM backend),
[ADR-002](ADR-002-book-as-root.md) (book as root — the decisions this BRD implies),
[feature-set.md](feature-set.md) (the catalogue), [examples](examples) (the originating incident)

This document is the **source of truth** for what Polyphony is meant to do. Change behaviour
here first, then in code. The `polyphony-canon` skill points here before any change to
generation behaviour.

---

## 1. Problem

Polyphony generates outlines and prose that ignore the author's actual book.

The originating incident (recorded verbatim in [docs/examples](examples)): an author supplied a
20,001-character synopsis for *Bored to Undeath* — a corporate-satire novel whose cast is Milo
Voss (a lich systems analyst), Zara Okafor (an uncontrollable sorceress), and Edric Thane (the
four-hundred-year-old Chief Executive Lich). Polyphony produced a 12-chapter outline that:

- starred **"Elara"**, a protagonist who appears nowhere in the source;
- read the in-world term **"the CEL" (Chief Executive Lich)** as a company name, *"Corporeal
  Energy Logistics"*;
- omitted Milo, Zara, Edric, Mr. Bones, and the entire storyboard;
- replaced a quiet character piece about consent and numbness with a generic chosen-one
  rebellion plot.

The author's verdict, in the file: *"THIS IS CLEARLY NOTHING LIKE MY DESCRIPTION."*

### 1.1 Root cause — this was not a prompt-quality problem

**The model received 2,000 of 20,001 synopsis characters (6.5%) and an empty character bible.**
Reproduced against the real code and the real document:

```
RAW SYNOPSIS         20,001 chars
REACHING THE MODEL    2,000 chars  (6.5%)
'Milo'   first appears at char  4,483  -> NEVER SENT
'Zara'   first appears at char  7,477  -> NEVER SENT
'Edric'  first appears at char 10,060  -> NEVER SENT
'CEL'    appears at char ~1,750        -> SENT, but its gloss
                                          ("Chief Executive Lich", char 10,093) was NOT
```

Everything the model got right lies inside the first 2,000 characters (spiritual attrition, the
Undeath Pipeline, sorcerous power, corporate drudgery). Everything it got wrong lies beyond
them. **"Elara" is the model filling a protagonist-shaped hole in a premise that described a
world with no people in it** — the only characterisation it received was the mages-vs-sorcerers
worldbuilding, so it produced a sorceress. Given an undefined acronym in a corporate context, it
back-formed a plausible company. Both are correct behaviour given catastrophically wrong input.

Four defects compound:

| # | Defect | Location | Effect |
|---|---|---|---|
| 1 | `sanitize_for_llm(synopsis, max_length=2000)` | `app/planning/outline.py:47` | Discards 93.5% of the book. The 2,000 is merely the *default* of a general-purpose helper, never sized for a synopsis. `books.synopsis` is unbounded `Text` and stores all 20k perfectly — then throws it away at prompt time. Gemini 2.5 Flash has a **1M-token** window. |
| 2 | `select(Character).where(Character.book_id == book.id)` | `app/api/plans.py:158`, `:425` | `book_id` is **never written by any code path**, so it is always NULL and these queries **always return zero rows**. The character bible is always `""`. Continuity's fact sheet ships an empty cast for the same reason. |
| 3 | Injection filter `r"--"` → `[FILTERED]` | `app/core/sanitization.py:46` | Rewrites the author's markdown `---` rules (12 occurrences in this document) as `[FILTERED]-`, corrupting the structure the model uses to parse sections. |
| 4 | `html.escape()` applied **after** truncation | `app/core/sanitization.py:56-63` | The model reads `It&#x27;s like learning an instrument`. The cap isn't even honoured (2000 → 2079). |

**These are one bug wearing four hats: the code treats the author's own prose as hostile
third-party input.** Injection scrubbing and XSS escaping are controls for *untrusted content
crossing a trust boundary*. Here the author's text goes to the author's own LLM call and returns
to the author. There is no boundary. The LLM is not a browser, and React already escapes on
render — so `&#x27;` exists purely to corrupt the model's input.

Defects 3 and 4 are **systemic, not outline-specific**. All nine `sanitize_for_llm` call sites
carry first-party prose — including `app/characters/context.py:83`, which caps *retrieved voice
samples* (the RAG grounding that is the product's whole premise) at **200 characters**.

### 1.2 Why nothing caught it

The evals are structurally incapable of seeing this class of defect:

- **The corpus never triggers it.** The `dracula` and `frankenstein` synopses are **451 and 607
  characters** against a 2,000 cap — 4× under. **The truncation branch has never executed in a
  single eval run.**
- **The bible is never populated.** `evals/offline.py:88` doesn't pass `character_bible` at all;
  `evals/steps/pipeline.py:165` creates a book with no characters. The whole prompt branch is
  dead under test.
- **The judge grades the wrong thing.** The rubric asks for "a clear inciting incident, rising
  complications, a midpoint turn, and a climax" — *structural shape*, not *fidelity to source
  entities*. **The "Elara" outline would score well.** It is a well-shaped outline for the wrong
  book.

The most recent outline commit (`f22527b`, "develop the premise faithfully") added prompt text
*begging* the model not to invent characters. It was tuned against a signal that structurally
cannot detect the defect, while the prompt pleaded for fidelity to a cast the pipeline had
already deleted. **Making the evals able to see this is a requirement, not cleanup.**

### 1.3 Structural cause — Book is not the root of anything

```
User
├── Manuscript(user_id) ──► Character(manuscript_id, CASCADE)   book_id: ALWAYS NULL
├── Book(user_id) ──► Chapter ──► Scene ; BookPlan ; PlotThread ; ContinuityReport
└── Character(user_id NOT NULL)  ◄── the real parent is User, not Book
```

Two parallel trees hang off `User`, and `Character` straddles both. `Book` has **no
`.characters` relationship at all**; no foreign key connects `Book` and `Manuscript`. The ORM
comment says the quiet part out loud: *"A character belongs to a user's bible… `book_id` scopes a
character to one book **when set**."* It is never set.

Consequences the author feels directly:

- **Character is the only canon entity.** There are no worldbuilding, style, or notes tables, so
  the magic system, the Undeath Pipeline, and Aeon Holdings have nowhere to live except crammed
  into a synopsis field that is then truncated to 6.5%.
- **`/characters` and `/manuscripts` are top-level UI routes with zero book awareness** — the
  characters page contains no reference to a book at all.
- **Vectors aren't book-scoped.** `voice_chunks.book_id` exists but is never passed by either
  caller; `retrieve_similar` has no `book_id` parameter, so it cannot filter by book even if it
  were populated.
- **Regeneration destroys work.** `plan.content = nodes` overwrites in place with no history.
  Generating an outline twice discards the first one and any hand edits, permanently.
- **Vector metadata is write-only.** The only way to fix one bad voice sample is to delete the
  entire character.

---

## 2. Users and goals

**Primary user**: a working novelist with a real book — a substantial premise, a named cast, and
opinions about voice — who wants machine help with structure and drafting **without the machine
overwriting their story**.

They are not prompting a chatbot for ideas. They arrive with 20,000 characters of storyboard
already written. The product's job is **fidelity and leverage**, in that order.

### 2.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | Generation is faithful to the author's canon | Principal-cast recall = 1.0; no invented protagonist; in-world terms read correctly |
| G2 | The book is the single root of every concept | No entity reachable except through a book; no NULL-scoped canon |
| G3 | Nothing the author wrote is ever silently destroyed | Every mutation versioned; regeneration appends; restore is non-destructive |
| G4 | The author can see and edit what the machine is using | Canon, vector chunks, and their metadata all viewable + editable; retrieval inspectable |
| G5 | Characters act, not merely speak | Every ensemble scene has non-empty actions for each present character |
| G6 | Free tier is usable and never loses work | Quota exhaustion pauses and resumes; no failed job from a 429 |
| G7 | Paying for capacity is a config flip | Graduation requires no code change |

### 2.2 Non-goals

- **Not a chatbot.** No open-ended "write me a story" surface. Everything is grounded in a book's canon.
- **Not multi-tenant collaboration.** One author owns a book. No sharing, so no untrusted-content boundary (see §5.2).
- **Not a general RAG platform.** Retrieval exists to serve character voice, not document Q&A.
- **Not a series/universe manager.** A character belongs to exactly one book. Reuse across books is an explicit copy, not a shared entity.
- **Not replacing the author.** The machine proposes; the author disposes. Extraction proposes for review; it never commits silently.

---

## 3. Terminology

Deliberately chosen to avoid vocabulary owned by other products or by our own sibling Greenlight
tools. **Avoid**: "Story Bible" and "Braindump" (Sudowrite), "Codex" and "Lore" (NovelCrafter),
"Muse" (our own tool), and Sudowrite's feature verbs (Describe / Expand / Twist / Canvas).

| Term | Meaning | Why this word |
|---|---|---|
| **Canon** | The book's authored truth: synopsis + characters + canon entries + style. | Literary *and* musical — a canon is a polyphonic form, so it is on-brand for Polyphony. Generic craft vocabulary; no product owns it. The fidelity design already reasons in terms of `canon_terms`. |
| **Canon entry** | One categorised worldbuilding fact. `category`: world / location / faction / item / concept / org. | One categorised table beats a set of branded section names, and extends without schema churn. |
| **Source** | Any raw input text attached to a book — an uploaded file *or* pasted text. | **Replaces `Manuscript` entirely** (§4.2). A manuscript and a pile of notes are the same thing: raw material that arrived somehow. |
| **Extraction** | Source → *proposed* canon, reviewed before commit. | Plain descriptive verb, unclaimed. |
| **Ensemble** | The narrator / character / editor scene loop. | Musical, on-brand, unclaimed. |
| **Voice chunk** | An embedded fragment of a character's speech or action. | Existing term; keep. |

---

## 4. Target model

### 4.1 Book as root

```
User
└── Book                          ← the root of every concept
    ├── Source          (upload | paste)  ──► voice chunks, extraction proposals
    ├── Character       (book_id NOT NULL) ──► voice chunks
    ├── Canon entry     (categorised worldbuilding)
    ├── Style guide     (one per book)
    ├── synopsis        (a field on Book)
    ├── Chapter ──► Scene ──► Beat / Revision
    ├── Plan            (outline | beat sheet)
    ├── Plot thread ──► Thread event
    └── Continuity report
```

Every concept is reachable only through a book. There is no user-level bible, no NULL-scoped
character, and no second tree.

### 4.2 Manuscript is rolled into upload

`Manuscript` disappears as a concept and a route. Uploading is not a separate mode of the
product — **you upload into a book**, and what you upload is a `Source`. A pasted pile of notes
and an uploaded `.docx` are the same entity with a different `kind`.

This is what collapses the two parallel trees rather than patching them. It also means the
free-form notes requirement needs no new entity: notes are a `Source` with `kind='paste'`, and
they feed extraction exactly like an uploaded manuscript does.

**A Source is disposable; the Canon is not.** Deleting a source file must never delete the cast —
the characters *are* the canon now, and the file was merely how they arrived. `characters.source_id`
is provenance only.

### 4.3 What feeds generation

Every generating feature assembles the **whole canon** and does not truncate it. A 20k-character
synopsis is ~5k tokens against a 1M-token window; the historical cap was never necessary. Where a
canon genuinely outgrows the window, the system **summarises a category** and says so — it never
silently cuts a string mid-sentence.

---

## 5. Requirements

### 5.1 Fidelity (G1)

- **R1.1** The complete synopsis, character canon, canon entries, and style guide reach the model on every generation. No truncation. No escaping. No pattern filtering.
- **R1.2** Outline generation is staged — skeleton → chapters → beats — with the whole canon in context at each stage.
- **R1.3** The skeleton stage must **restate the premise in its own words** before structuring. This surfaces a misreading (e.g. "the CEL is Corporeal Energy Logistics") at stage one, for the price of one call, instead of after twelve chapters are built on top of it.
- **R1.4** **Hard gate — principal-cast recall.** Every character whose role is protagonist / antagonist / main must appear in the outline. Exact match on names and aliases: deterministic, free, no false positives. Below 1.0 → regenerate once → then warn. *"Elara" was a recall failure — the real protagonist was absent — and recall is the reliable signal.*
- **R1.5** **Soft warning — unknown proper nouns.** A good outline legitimately invents an innkeeper, so unknown names **warn, never block**. Structural cast fields are strict; names in prose summaries are advisory.
- **R1.6** Never hard-fail an expensive job on a heuristic. Always save the artifact with warnings attached.

### 5.2 Text handling (G1)

- **R2.1** First-party author content is passed to the model **unaltered** except for control-character stripping and newline normalisation.
- **R2.2** **Overflow raises; it never truncates.** A silent cap is precisely what kept this bug invisible for its entire life. Callers needing a bound handle the error explicitly.
- **R2.3** Prompt-injection defence is **structural, not lexical**: author content is fenced in a delimited block the model is told to treat as story material, never as instructions. Not one character of the author's prose is altered. *Rationale: there is no untrusted-content boundary in a single-author product (§2.2). Should sharing ever ship, this fence — not a regex that eats em-dashes — is the control that scales.*

### 5.3 Book as root (G2)

- **R3.1** `characters.book_id` is `NOT NULL`, `ON DELETE CASCADE`.
- **R3.2** Character names are unique within a book. *(Today manual characters have no uniqueness at all: the index is keyed on a NULL `manuscript_id`, and NULLs are distinct in Postgres. Cast fidelity depends on fixing this.)*
- **R3.3** Voice chunks carry `book_id` and retrieval can filter by it.
- **R3.4** Characters and sources are reached through the book in the UI, not as top-level routes.

### 5.4 Nothing is destroyed (G3)

- **R4.1** Every mutation of canon or a generated plan appends a version first.
- **R4.2** Regeneration appends; it never overwrites. The prior generation stays browsable.
- **R4.3** Restore is **forward-only**: restoring v2 appends v4 carrying v2's content. History is never rewritten or deleted.
- **R4.4** Extraction proposes; the author approves per item before anything is written.

### 5.5 Visibility and control (G4)

- **R5.1** Canon is fully viewable and editable, with a text editor sized for real prose.
- **R5.2** Voice chunks and their metadata are listable, editable, and deletable, with re-embedding on edit. *(Today the store is write-only: the only fix for one bad sample is deleting the whole character.)*
- **R5.3** Retrieval is inspectable — which chunks grounded a generation, and at what similarity. *(The score is already computed and discarded.)*
- **R5.4** Structured character fields (`personality_traits`, `voice_characteristics`, `relationships`) are editable. *(Today they have no UI at all.)*

### 5.6 Characters act (G5)

- **R6.1** A character's contribution to a scene is `{intent, actions, lines, interiority}` with **`actions` required and non-empty**. A character may say nothing and still be present.
- **R6.2** Action grounding is retrieved **separately** from dialogue grounding, so dialogue cannot crowd it out.
- **R6.3** The story editor verifies every present character acted.

*Today `_generate_action` derives action **from** dialogue after the fact — exactly the wrong
dependency. Action must be first-class in the request, not a garnish on the reply.*

### 5.7 Tiering (G6, G7)

- **R7.1** Free tier is the default and must be genuinely usable.
- **R7.2** **Quota exhaustion pauses; it never fails.** A 429 / `RESOURCE_EXHAUSTED` inside a job re-queues it for when quota returns, honouring the server's suggested retry interval. The author loses nothing and re-clicks nothing.
- **R7.3** Multi-call jobs preflight against the remaining budget and refuse to start rather than half-write.
- **R7.4** Degradation is graceful and explicit: staged outline → single-call outline; ensemble → prose mode.
- **R7.5** Cost is shown before an expensive action is taken.
- **R7.6** **Graduation to paid is a config flip, not a code change**: one tier setting raises rate limits, lifts caps, and enables the expensive paths.

---

## 6. Constraints

- **One container**, 1 OCPU / 6 GB, OCI Always-Free, behind a Cloudflare tunnel (ADR-001).
- **One Postgres** (Neon) holding relational data *and* vectors (pgvector, 384-dim, HNSW).
- **Gemini free tier by default**: `max_rpm=8`, and — importantly — a **requests-per-day** ceiling.
- **Self-imposed** `USER_DAILY_TOKEN_LIMIT = 200_000`.
- The LLM backend is **provider-fungible** (ADR-001 §2): Gemini / Groq / xAI / OpenAI behind one OpenAI-compatible registry. Paid capacity is a provider + key + tier setting. **Never rebuild this.**

### 6.1 Measured: the ensemble is not free-tier-viable as naively specified

Every agent seeing the whole canon, one 3-character scene:

| chars | rounds | calls | pacing wait | input tokens | % of daily budget |
|---|---|---|---|---|---|
| 3 | 1 | 10 | 75 s | 50,000 | 25% |
| 3 | 2 | 20 | 150 s | 100,000 | **50%** |
| 3 | 2 | 20 | 150 s | **39,600 (scoped)** | **20%** |

Two scenes a day is not a product. Scoping character agents to their own bio, the scene brief,
and their own retrieved chunks (~800 tokens) — reserving the full canon for the narrator and the
editor — cuts this to ~20%. **This is also more correct in fiction: a character does not know the
plot.** The constraint and the craft agree.

⚠️ **The binding limit is probably requests-per-day, not tokens.** The 200k token limit is our own
number and is cheap to raise; RPD is the provider's and is shared across all users. At ~12 calls
per ensemble scene this is a low double-digit ceiling site-wide. **Verify the current free-tier
RPD before building the ensemble.** If it is too low, the answer is the paid flip (R7.6), not a
redesign.

---

## 7. Success criteria

**The acceptance test for the whole effort** — load the real *Bored to Undeath* storyboard from
[docs/examples](examples), generate a 12-chapter outline, and assert it stars **Milo Voss** and
**Zara Okafor** against **Edric Thane** in **Aeon Holdings**, with no invented protagonist and
"the CEL" understood as the **Chief Executive Lich**.

Supporting gates:

| Criterion | Gate |
|---|---|
| Full synopsis reaches the model | Unit test, no LLM: assemble the prompt from a 20k synopsis and assert the tail survives |
| Invented protagonists are caught | `cast_fidelity ≥ 0.9` on the `cel` corpus fails CI; a hand-written "Elara outline" fixture scores ≈ 0 |
| The eval can see the bug at all | A corpus whose synopsis exceeds the historical cap and whose cast first appears past char 4,000 |
| Regeneration is non-destructive | Generate twice; v1 is listed and restorable |
| Quota never loses work | Force a 429 mid-job; the job re-queues and completes on resume, with no duplicate spend |
| Paid graduation | Flip the tier; caps lift with no code change |

---

## 8. Out of scope for now

Recorded so they are decisions, not oversights:

- **Versioning voice chunks** — they are derived, and re-embedding is idempotent. A chunk text edit is destructive; accepted for now.
- **Version pruning** — no pruning initially. This makes one rule hard: **the ensemble must never snapshot per agent turn**, only the final scene.
- **Unifying `scene_revisions` with `entity_versions`** — scene prose is large, high-volume text with a different access pattern. Two mechanisms is deliberate (see ADR-002).
- **Series / shared universes** — a character belongs to one book (§2.2).
- **Concurrent job execution** — the worker runs one job at a time, so a long ensemble blocks other users' scenes. Acceptable at this scale; revisit if it bites.
