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

**These are one bug wearing four hats: the code applies the wrong controls at the wrong sink.**
XSS escaping is an *output-encoding* control and belongs at the HTML renderer, not on the way
into a prompt — an LLM is not an HTML sink, so `&#x27;` bought nothing and corrupted the input.
`r"--"` is a *SQL* control applied to a prompt. And a regex blocklist of injection phrases is a
control that does not work: it is trivially bypassed by paraphrase or encoding, so it bought ~no
security while demonstrably eating a book.

**This is not an argument that the content is trusted.** It isn't — see §5.2.

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
- **Not multi-tenant collaboration.** One author owns a book. This bounds an injection's blast radius to the injecting user's own book — it does **not** mean content is trusted (§5.2).
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

- **R1.1** The complete synopsis, character canon, canon entries, and style guide reach the model on every generation — outline (staged and single-call), scene beat planning and prose (both modes), continuity, and the ensemble's narrator and editor. No truncation. No escaping. No pattern filtering. A scene's cast is a set of characters of the book, validated at the API; an unknown name is rejected, never generated voice-blind. The beat-cast blocks carry voice samples and movement only; the bible is sent once, in the canon block (SPEC-26). Continuity reads the book scene by scene. Each finding quotes the prose; the server finds the scene the quote is in and the report links to it, opening that scene in the editor. A finding whose quote cannot be found is kept and says "could not locate" (SPEC-31). Before the chunks, a deterministic **era ledger** reads every year the prose uses as a date (an elided `17—`, a month's year, "in the year 1797", "A.D. 1797", a dateline such as "Paris, 1889."; never "born in 1987", "1500 men" or a currency sum, nor a year inside double-quoted dialogue — SPEC-91 W16-5); when at least 5 dates are found and 75% or more share a century, each date in another century is a minor timeline finding quoting it (SPEC-90 W15-7).
- **R1.1a** A beat is written as that beat. The prose prompt carries the scene's whole beat plan with the current beat marked, and tells the model to stop where the next beat begins. Prior text (the scene so far, and the previous scene's tail) keeps its paragraph breaks and never opens mid-sentence. The book's sample prose is the primary style reference. Generic house style is used only when the style guide has neither tone nor sample prose. A beat's opening sentence that exactly repeats the end of the prior text is trimmed (deterministic, never empties a beat). *(SPEC-18)*
- **R1.1b** **Planned beats are data** (SPEC-23). A promoted chapter stores its outline node's children as `planned_beats` (`{node_id, title, summary, character_ids, secondary_names, setting, tone}`), not as text in its summary. "Generate all planned scenes" writes each scene from its beat: the beat's cast (else the chapter's; an explicit request cast overrides every beat) plus the chapter's POV character, and the beat's setting and tone. An empty tone stays empty; the style guide governs, and "neutral" is never injected. Editing the summary does not change what is generated. The chapter PATCH edits the beats strictly (an id outside the book's live cast is a 422) and versions them. Promoted titles drop the outline's "Chapter N:" prefix. A chapter promoted before this change still generates from its legacy "Planned beats:" summary lines. The scene modal sends exactly the cast it shows: picking a planned beat shows the cast the server resolves for it, and the chapter's point-of-view character is always included (SPEC-32). Before generating, the author edits the chapter's title and summary and each beat's title, summary, cast, setting and tone, and adds or deletes beats, in one form opened from the chapter row and saved by one PATCH carrying only what changed; a written beat links to its scene, and editing it never changes that scene. The generate modal and the beat editor share one cast-chip control, so the chapter's POV character cannot be removed in either (SPEC-35).
- **R1.1c** A beat's scene is planned from its own cast: the planner sees the cast as *available* (not all need appear; unnamed functional figures allowed), and a blank setting or a blank tone is omitted from the prompt so the style guide governs (a tone the author chose, "neutral" included, is printed). The prose prompt states the POV mode and character and narrates only what that character perceives or knows; the last beat ends inside the moment, with no summary of what follows. Ensemble mode builds its own prompts and is not covered. *(SPEC-24)* The beat planner and revise carry the same POV paragraph: the plan stays in the POV character's head and has them learn a name before the prose uses it; a wide (omniscient, objective) mode is not held to one head. *(SPEC-24 wave 3)* A one-head POV paragraph also says the brief may say what other people intend or feel, but the prose shows only what the POV character can see or hear. *(SPEC-36)* A sample from later in its source's plot than the scene being written is not shown, and a minor character's introduction stops before its later reveals; the whole synopsis still reaches every call (R1.1). *(SPEC-40)* Each scene's planner sees the chapter's other planned scenes and what the earlier ones planned. *(SPEC-43)* Drafted prose and revisions name only people the book knows and narrate no hindsight; a generated scene lists the names it used that the canon does not know. *(SPEC-48)* A voice sample that scripts the beat's own line is not shown. *(SPEC-46)* The beat planner is asked to describe what people say rather than quote the author's source or samples. *(SPEC-61)*
- **R1.1d** The planner, beat and revise prompts say where the scene sits ("chapter N of M") and that nothing after this chapter has happened yet; the synopsis stays whole. *(SPEC-30)*
- **R1.2** Outline generation is staged — skeleton → chapters → beats — with the whole canon in context at each stage.
- **R1.2a** Each outline stage sees the plan it details. The beats stage receives the premise (authoritative when the author confirmed or edited it), the central conflict, and its act's title, goal and turn. Acts are numbered from 1 in prompt text. Structures embedded in a prompt are JSON, never a language-specific repr. *(SPEC-18)* Each act's beats prompt also lists every chapter of the outline and the beat titles earlier acts already have, and asks that no one be introduced before the chapter that introduces them. *(SPEC-38)* A scene brief says only what the chapter's POV character can witness, never what they fail to notice or will later learn, and the outline audit warns on one that does; a chapter's people include everyone its scenes name. *(SPEC-49)* Both the chapters and the beats stage print one line asking that each summary be what the chapter's POV character witnesses or learns. *(SPEC-63)*
- **R1.3** The skeleton stage must **restate the premise in its own words** before structuring. This surfaces a misreading (e.g. "the CEL is Corporeal Energy Logistics") at stage one, for the price of one call, instead of after twelve chapters are built on top of it, and is shown to the author for confirmation before the chapters stage (on the free tier by default; an edited premise is authoritative for the stages after it).
- **R1.4** **Hard gate — principal-cast recall.** Every character whose role is protagonist / antagonist / main must appear in the outline. Exact match on names and aliases (a character's recorded `aliases` count — "the CEL" is Edric Thane): deterministic, free, no false positives. Below 1.0 → regenerate once → then warn. *"Elara" was a recall failure — the real protagonist was absent — and recall is the reliable signal.*
- **R1.5** **Soft warning — unknown proper nouns.** A good outline legitimately invents an innkeeper, so unknown names **warn, never block**. Structural cast fields are strict; names in prose summaries are advisory. The warning reads only non-title outline text and treats every capitalised phrase in the canon's synopsis, cast, entries and style as known (SPEC-21). A word written entirely in capitals (a sign or a stamp) is never reported as a possible invention (SPEC-33). A capitalised run followed by a label token ("Appendix F", "Form 9") is not a name. Mid-sentence, a capitalised function word before one word opens a title ("a slide called Our Values") and is not reported; before two or more words it is trimmed, so "The Marrow Exchange" is reported. A one-word name after a mid-sentence "The" is therefore missed (SPEC-39). An abbreviated honorific ("Mr."), a weekday plural ("Fridays") or a phrase joined by "and" or "of" is never split into separate warnings or reported as a name (SPEC-44).
- **R1.5a** **Named secondaries are allowed; principals are still gated.** The outline may name people the synopsis or canon entries name who are not in the cast; it may not invent others, and `pov` stays canon cast. Each chapter and beat lists its `people`; the server classifies them once (cast → `character_ids`, named by the canon text → `secondary_names`, anyone else is dropped from the saved outline and reported by the invented-names warning). The outline offers "Add <name> to cast" only for declared secondaries that the canon text names and the cast lacks (`suggested_characters` on `GET /plans` and both promote routes; advisory, so an error yields `[]`). *(SPEC-24, SPEC-23)*
- **R1.5b** A scene's named secondaries reach the planner, every beat prompt and revise with the sentence that introduces them and up to three of their own lines from the book's sources (or a note to keep them brief), never with a principal's line. *(SPEC-29)*
- **R1.6** Never hard-fail an expensive job on a heuristic. Always save the artifact with warnings attached.

### 5.2 Text handling (G1)

**Threat model, stated plainly: content reaching the model is NOT all trusted.** Polyphony
ingests `.pdf`, `.html`, `.docx`, `.txt` (`ALLOWED_EXTENSIONS`), and nobody authors a PDF
keystroke by keystroke — uploaded documents arrive from elsewhere and are untrusted. The service
is publicly reachable. **The frontend is never a security control**: every requirement below is
enforced server-side and holds regardless of what any client does.

What makes permissiveness *safe* is not trust — it is **capability containment**:

- These calls **generate text**. No tool use, no code execution, no privileged action is driven by model output.
- Output passes **strict validators** into text fields (`validate_outline_nodes`, `extract_json_array`) — never eval'd, never shelled, never turned into SQL.
- Everything is **scoped to one user's own book**, so an injected upload corrupts the outline of whoever uploaded it and cannot reach another tenant.

Worst case is "your outline is weird" — self-inflicted and unprivileged. **If that stops being
true — model output gains tools, drives privileged actions, or crosses tenants — this trade must
be re-examined, not inherited.**

- **R2.1** Content is passed to the model **structure-preserving**: control-character stripping, newline normalisation, and escaping of chat-template control tokens (escaped, never deleted). No HTML escaping (wrong sink — encode at the renderer). No phrase blocklists.
- **R2.2** **Overflow raises; it never truncates.** A silent cap is precisely what kept this bug invisible for its entire life. Bounds are explicit, generous, per-purpose cost/DoS controls; callers handle the error in the open.
- **R2.3** Injection defence is **structural, not lexical** (OWASP LLM01): untrusted content is fenced in a labelled block and the model is instructed to treat it as story material, never as instructions — *spotlighting*. Not one character of prose is altered. **Lexical blocklists are rejected on the merits**: trivially bypassed, so ~zero security, while this one rewrote every markdown `---` into `[FILTERED]-`. A control with no benefit and large collateral damage is not defence in depth; it is damage.
- **R2.4** Model output is treated as untrusted at its point of use — validated against a schema before it becomes data.

### 5.3 Book as root (G2)

- **R3.1** `characters.book_id` is `NOT NULL`, `ON DELETE CASCADE`. So is `scenes.book_id`:
  a scene goes with its book (and its beats and revisions with it), and deleting a chapter
  never silently orphans prose — the author moves its scenes to another chapter or deletes
  them, and a bare delete of a chapter with scenes is refused (409). *(SPEC-04)*
- **R3.2** Character names are unique within a book. *(Today manual characters have no uniqueness at all: the index is keyed on a NULL `manuscript_id`, and NULLs are distinct in Postgres. Cast fidelity depends on fixing this.)*
- **R3.3** Voice chunks carry `book_id` and retrieval can filter by it.
- **R3.4** Characters and sources are reached through the book in the UI, not as top-level routes.

### 5.4 Nothing is destroyed (G3)

- **R4.1** Every mutation of canon or a generated plan appends a version first.
- **R4.2** Regeneration appends; it never overwrites. The prior generation stays browsable. The Outline tab's History previews and restores earlier outlines (SPEC-38).
- **R4.3** Restore is **forward-only**: restoring v2 appends v4 carrying v2's content. History is never rewritten or deleted.
- **R4.4** Extraction proposes; the author approves per item before anything is written. **Extraction works at manuscript scale** (SPEC-09): the source is read as a map-reduce — ~12k-token windows on paragraph boundaries (one fast-model JSON call each, checkpointed per window so a quota pause resumes rather than re-spends), a deterministic alias- and honorific-aware merge across windows, then batched calls (~10 characters each, bounded) that write the **full** proposal — role (closed vocabulary or `other: …`), description, goals, arc, relationships, personality, voice, aliases — and the synopsis last, from the merged canon. A window whose answer is truncated is retried once as smaller windows and, if still unreadable, skipped with a warning on the proposals — **it never fails the run**. Proposals carry `mentions` and `windows`; the review sorts by them and pre-deselects names seen in only one window unless flagged as a narrator (still listed, never hidden). **Cost is shown before the run**: every enqueueing endpoint returns the estimate (windows, requests, tokens), and a source that cannot finish within what the author can spend while the job waits out quota (`JOB_MAX_PAUSE_HOURS`) is refused with an "extract the first N chapters" alternative — an upload or paste is still saved. Canon-entry categories are validated against the set (an unknown category is stored as `concept`). Reduce writes each character from up to 8 evidence quotes, their own attributed dialogue (up to 6 lines) and, for a one-window source, the source itself. Mention counts include an unambiguous given name or surname; pronouns are not counted, and the review says "name mentions" (SPEC-26). A character with no quote in a window is given "no verbatim quote in part N" as evidence; "narrates part N" is kept for narrators (SPEC-64). **A canonical name is a proper name as written in the source when one exists**: a descriptor ("my dear sister", "Fenwick's father") or a note ("Robert (not used)") never outranks it; notes in parentheses are stripped from names and aliases, a pronoun is never an alias, a shared descriptor alias ("the stranger", "the young Englishman") never links two people while a shared name with a particle ("Van Helsing") still does, and a reduce alias that names another proposal is dropped. A being the text never names keeps the label the text uses most; no name is invented (SPEC-90 W15-5).
- **R4.5** An approved item is **never silently dropped, and is written exactly as reviewed**. An approved character, canon entry, style, or synopsis whose name already exists is **merged** and versioned. A field is applied **whenever supplied — including `""`/null, which clears it** — and left untouched only when **omitted**, so an author can delete a role, description, style field, or synopsis. A proposal also merges into an existing character it **names by alias** (its name is one of the character's aliases, or it lists the character's name as an alias — unambiguously; a shared alias alone never merges two people). `aliases` is the one field that **unions** rather than replaces (a later import never drops an alias the author recorded; a supplied `null` clears). Blank-named items are **surfaced** (reported in the response's `skipped`), never silently filtered by the client. The commit response reports `created` / `updated` / `skipped` per type, and the review UI must not signal success for an item the commit discarded. *(Earlier passes merged only characters, applied only truthy fields — so a cleared value silently reverted — and let the client drop blank names before the server ever saw them.)*
- **R4.6** A character a source contributed to stays **reachable from that source** even when it was merged into an existing (differently-sourced or manually-created) character. Source→character provenance is a **many-to-many association written at reviewed commit**, not the single-valued `Character.source_id` (which records only the first source and goes NULL when that file is deleted). The source-scoped cast query and the generation picker both resolve through the association. *(Deleting a source still never deletes the cast — R3-consistent — the association row simply cascades away.)*
- **R4.7** An extraction whose proposals were not yet committed is **resumable from its source**: the source exposes its latest extraction run and status, and navigating away from the review never strands the run or forces a duplicate extraction.
- **R4.8** **Commit is exactly-once.** The extraction run is locked for the transaction and only a `ready` run is accepted; a double-submit or a retry after a lost response is rejected (conflict) rather than appending duplicate versions or re-spending embedding budget. The review UI treats an already-committed run as done — it resumes to the book, not a dead form.
- **R4.9** **AI revision proposes; the author decides** (SPEC-19). `POST /books/scenes/{id}/revise` revises a range, a beat, or the whole scene as a job. The job is preflighted and estimated, deduped per scene, pauses on quota and can be cancelled. Its result is a proposal (`SceneRevision(source="ai_revise")`) spliced server-side, so text outside the range is byte-identical. The draft changes only on accept, which snapshots the previous draft first and is refused if the draft changed since the proposal was written. Discard changes nothing. The revise prompt carries the whole canon, the scene's cast samples, and the rest of the draft as read-only `<prior>`/`<after>`. A name the revision introduces that is outside the canon and the scene is a warning, never a failure.
- **R4.10** **Revise from the editor** (SPEC-25). The scene editor requests an AI revision of a selection, a beat ("Redo beat N", from the scene's stored beats) or the whole scene, and shows the proposal as a side-by-side word diff with Accept and Discard. Only the saved draft is revised: with unsaved edits, or while the scene is generating, the request is refused. The draft revised is the raw editable text (edge whitespace kept), and a stale view is a 409. Nothing changes until Accept, which keeps the previous draft as one more revision; Discard leaves the editor text byte-identical. Proposals are not revisions: the Revisions list shows snapshots only. A redo of one beat sees the scene's whole plan with that beat marked and the terms the rest of the chapter refers to, and the proposal warns when it drops one. *(SPEC-41)* A redo with a direction changes only what was asked and keeps every other sentence; each term to keep shows the phrase the chapter uses it in. *(SPEC-47)* A request to cut, tighten, trim, shorten, condense or pare adds a line asking to cut every word that adds nothing while keeping every fact, image, joke, aside and voice marker. *(SPEC-59)*

### 5.5 Visibility and control (G4)

- **R5.1** Canon is fully viewable and editable, with a text editor sized for real prose.
- **R5.2** Voice chunks and their metadata are listable, editable, and deletable, with re-embedding on edit. *(Today the store is write-only: the only fix for one bad sample is deleting the whole character.)*
- **R5.3** Retrieval is inspectable — which chunks grounded a generation, and at what similarity. *(The score is already computed and discarded.)*
- **R5.4** Structured character fields (`personality_traits`, `voice_characteristics`, `relationships`) are editable. *(Today they have no UI at all.)*
- **R5.5** The scene editor never loses unsaved work. Leaving with unsaved changes (in-app link, Back, tab close) asks first. The text autosaves locally every ≤5 s without creating server revisions and is offered back on reopen ("Restore unsaved draft from HH:MM"). Saving clears the draft. *(SPEC-16)* The editor heads the scene with its chapter number and title and the scene's title, renamed in place (a title is not versioned), and Previous / Next walk the book through the same guard. A generated scene is named after its planned beat, else the first six words of its description, and every scene list uses one book order. *(SPEC-37)*

### 5.6 Characters act (G5)

- **R6.1** A character's contribution to a scene is `{intent, actions, lines, interiority}` with **`actions` required and non-empty**. A character may say nothing and still be present.
- **R6.2** Action grounding is retrieved **separately** from dialogue grounding, so dialogue cannot crowd it out. *(Each agent gets a `<voice>` block of their own words and a `<physicality>` block of action/description samples. Since SPEC-17 `<voice>` never falls back; `<physicality>` falls back to untyped retrieval when it has fewer than two typed hits, never repeating dialogue or the voice samples — `app/characters/context.py::character_samples`, shared by `build_agent_context`, the prose and revise prompts and the voice workbench's "Test a line". A movement line it withholds is recorded with its rule in the scene's grounding, and a prompt asks to match voice samples only when it shows one — SPEC-69, SPEC-70.)*
- **R6.3** The story editor verifies every present character acted.
- **R6.4** **Voice samples are the character's own words** (SPEC-17). Dialogue is attributed only from a tag adjacent to the quote in the same paragraph (or an unambiguous action beat or exchange); a paragraph carrying anyone else's speech is not indexed for the character. Precision is ≥ 0.95 on a hand-labelled fixture, which gates CI. The prose prompt separates "Voice samples (their own words)" (dialogue, plus the character's own first-person narration) from "How they move" (action, description, thought centred on the character). Existing books are re-indexed with `python -m app.cli reindex-voice --book <id>`. A voice sample is at least 3 words. The voice workbench's "Test a line" grounds on the same samples, through the same retrieval and renderer, as scene prose, and shows the grounding rows it used (SPEC-17 wave 3). **Samples teach manner, not material** (SPEC-36): both blocks are labelled for manner only, and a movement sample that shares four content words with the scene's brief (it narrates the event being written) is dropped and not replaced. Voice samples are never filtered this way. An **embedded narrative** — an uncredited quote kept open across paragraphs, each opening a quote and only the last closing it, that follows a heading or chapter start, a letter or document header, or a line ending in ":", "--" or "—" — is narration, not speech: the cast it names is indexed as movement, except in a paragraph that quotes someone inside it ('…' of 3+ words). Anywhere else such a run stays speech, and a quote whose first paragraph is tagged stays its speaker's (SPEC-90 W15-6). A line ending in a speech verb and a colon ("said:", "asked:", "began:", "replied:", "continued:", "answered:", "cried:") opens its setup speaker's speech, not a tale: the run is credited to that line's tag ("the creature said:"), or to no one ("She leaned forward and said:"). An embedded narrative is the first-person voice of its teller when the text names them — a self-naming inside it ("my name is X") or the line introducing it ("the stranger began his tale") — and of no one otherwise. **First-person narration** without narrator headings is read chapter by chapter: a chapter's "I" is the one cast member it self-names, else the one member other speakers address there ("My dear Vandel," cried he) who never speaks by name in it; that member's first-person paragraphs there are theirs. Only a chapter with no identified "I" goes to the narrator role (or the text's one self-named member): the narrator role never takes another narrator's chapter or an embedded tale (SPEC-91 W16-2).

*Today `_generate_action` derives action **from** dialogue after the fact — exactly the wrong
dependency. Action must be first-class in the request, not a garnish on the reply.*

### 5.7 Tiering (G6, G7)

- **R7.1** Free tier is the default and must be genuinely usable.
- **R7.2** **Quota exhaustion pauses; it never fails.** A 429 / `RESOURCE_EXHAUSTED` inside a job re-queues it for when quota returns, honouring the server's suggested retry interval. The author loses nothing and re-clicks nothing. **Every job resumes from its last completed model call; a canon change during a pause restarts the job** (and says so on the job), because mixing work generated against two versions of the book is worse than paying twice (SPEC-08). *(A continuity check paused across the SPEC-31 deploy restarts once, because its input hash now covers the scene-marked chunk texts.)* **A pause is visible on the domain object with an expected resume time, is bounded, and is cancellable** (SPEC-07): scenes, plans, extraction runs and continuity reports show `paused` with "resumes ~HH:MM"; a job that makes no progress for `JOB_MAX_PAUSE_HOURS` (default 48) fails with "quota did not return within 48h"; the author can cancel a queued, paused or running job (a running one stops at its next model-call seam). A duplicate enqueue returns the in-flight job instead of spending again.
- **R7.3** Multi-call jobs preflight against the remaining budget and refuse to start rather than half-write. A planned batch is the one exception, by design: it queues the scenes that fit, in beat order, says how many it deferred and why, and is refused only when not even its first scene fits; generating again later writes the rest, skipping written beats (SPEC-34). Cached input tokens are not measured yet; weighting them is a wave-5 item.
- **R7.4** Degradation is graceful and explicit: staged outline → single-call outline; ensemble → prose mode.
- **R7.5** Cost is shown before an expensive action is taken.
- **R7.6** **Graduation to paid is a config flip, not a code change**: one tier setting (`LLM_TIER`) raises rate limits, lifts caps, stops local requests-per-day tracking, and enables the expensive paths. Explicit overrides (`LLM_MAX_RPM`, `LLM_MAX_RPD`, `USER_DAILY_TOKEN_LIMIT`) apply only when set. `MONTHLY_COST_CEILING_USD`, when set, is enforced deployment-wide over a trailing 30 days, before every call (SPEC-81; its stated overshoot): past the ceiling, queued work **pauses, up to the 48 h pause bound, and then fails** with the reason named, and a re-run starts over; a model with no `PRICES` row fails its job.
- **R7.7** **The provider's requests-per-day ceiling is enforced before the call** (`app/llm/ledger.py`), so a job pauses until the vendor's day rolls over instead of retrying into a wall. A quota 429 never trips the circuit breaker; an open breaker (a provider outage), or a connection error, timeout or 5xx that outlasts the per-call retries, pauses jobs on every tier (W14-8).
- **R7.8** **The free tier's model is Gemini 3.5 Flash-Lite for the whole pipeline** (outline, extraction, continuity, prose; 500 requests a day), with Gemini 3.6 Flash (20 a day) as an author's opt-in for scene writing only (generate, generate-planned/ensemble, revise) (U1, 2026-10-21). The registry's per-model daily table is the allow-list, with the running default first; any other model is refused (422). An operator's `LLM_MODEL` outside that table is selectable but untracked unless `LLM_MAX_RPD` is set. The choice rides on the job, so a paused job resumes on the model it started with and every scene of a batch uses the same model; each scene records the model that wrote it, and the picker shows how many flash generations are left today, from the same estimate the preflight enforces. **The per-book choice is remembered per browser**, not on the server: another device shows the default until the author picks again. Server-side persistence would need a migration and is a separate item, offered to the user, not built.
- **R7.9** **Named authors write with Claude** (user, 2026-10-22): an author whose email is in `ANTHROPIC_USERS` (or under an `@domain` entry there, as the evals' `@polyphony.eval` users are on a `claude=yes` run) has every call served by Anthropic (Claude Opus 5.5 by default, Claude Sonnet 5.5 selectable for scene writing) when `ANTHROPIC_API_KEY` is set; everyone else stays on `LLM_PROVIDER`. It is paid: its models carry prices, so `MONTHLY_COST_CEILING_USD` holds it, and the tier's per-author token cap still applies. A refusal fails the job with its reason and is not retried. A model remembered from the other provider falls back to the author's own default.

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

**Decision (2026-10, SPEC-14):** ensemble stays paid-tier-only. The free tier's requests-per-day ceiling is now tracked (R7.7), every scene request is preflighted against it, and an ensemble request that cannot finish within the provider's remaining requests is refused with its estimate rather than paused mid-loop. The `ensemble` eval step compares the same scene in both modes (action coverage + judge); run it with `LLM_TIER=paid` and a live key to revisit the gating with evidence.

⚠️ **The binding limit is probably requests-per-day, not tokens.** *(Now tracked per model: 500/day for 3.5 Flash-Lite and 20/day for 3.6 Flash, the free-tier `quotaValue`s measured on 2026-10-20 (W13-0, run 39495277558), reset at midnight Pacific. Google no longer publishes per-model numbers (limits are per project and shown in AI Studio); `LLM_MAX_RPD` overrides when a project's limit differs.)* The 200k token limit is our own
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
| Quota never loses work | Force a 429 mid-job; the job re-queues and completes on resume with no lost work and no repeated model call (see R7.2) |
| Paid graduation | Flip the tier; caps lift with no code change |

**Evaluation (SPEC-22).** The `cel` eval runs the author path — outline → premise confirmation →
promote → planned scenes — in a book with real cast entries, a style guide and a voice-bearing
source, and grades each artifact on the R1–R6 rubrics with a judge that is not the model under
test. Deterministic prompt-contract and seam checks over the relay archive (what the app actually
sent the model) are the primary before/after signal, because they do not depend on model variance.

**Evaluation v3 (SPEC-28).** The scene R1 cap fires on "a planned principal neither named nor the
attributed speaker of a quoted line, or no cast on the page"; a principal who speaks but is never
named is a recorded warning, not a cap, and the judge's R1 stands. The prompt contract covers every
wave-2 prompt block (P1–P10), and a check with no matching call reports `rate: None` rather than a
score. The eval adds a `revise` step: three revisions of one scene, each checked for byte-identical
text outside the range and no new names, and judged on RA (instruction adherence) and R5.

**Evaluation v4 (SPEC-33).** Extraction precision counts the corpus' secondary cast
(`secondary_cast` in the ground truth) as hits, while recall stays over the principals. The
mention check is `principal_mentions_min` ≥ 10, the fewest name mentions of any principal. The
attribution step reports `gap` (top-1 minus top-2 score) and `mean_gap`, and its probe is an
in-world line from the ground truth. The continuity control is graded against the corpus canon,
not a placeholder synopsis.

**Evaluation v5 (SPEC-39).** There is no fallback scene: when no promoted chapter can be written,
the prose step reports `path: "failed"` and is not cached. It judges every planned scene, up to
five, and reports the batch's `queued`, `deferred` and `estimate_over_actual`. Gold utterances are
whole, voice probes are per role, and the prompt contract adds `sample_lift` (no word 8-gram of a
prompt's samples in its answer; W3 baseline 2/20 under the wave-4 label) and
`beats_see_whole_outline`.

---

## 8. Out of scope for now

Recorded so they are decisions, not oversights:

- **Versioning voice chunks** — they are derived, and re-embedding is idempotent. A chunk text edit is destructive; accepted for now.
- **Version pruning** — no pruning initially. This makes one rule hard: **the ensemble must never snapshot per agent turn**, only the final scene.
- **Unifying `scene_revisions` with `entity_versions`** — scene prose is large, high-volume text with a different access pattern. Two mechanisms is deliberate (see ADR-002).
- **Series / shared universes** — a character belongs to one book (§2.2).
- **Concurrent job execution** — the worker runs one job at a time, so a long ensemble blocks other users' scenes. Acceptable at this scale; revisit if it bites.
