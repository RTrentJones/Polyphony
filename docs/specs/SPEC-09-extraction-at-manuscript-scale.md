# SPEC-09 — Extraction at manuscript scale

**Closes**: F8, eval-audit backlog items 1 and 2 · **Size**: L · **Depends on**: SPEC-03,
SPEC-07 (and SPEC-08 for resumable chunks)

## Why

`extract_canon` sends the entire source in one call with a 4,096-token answer. A real manuscript is
roughly 120k input tokens, more than half a user's daily budget, with no preflight; the answer for a
real cast and world does not fit, so the JSON truncates, parsing fails, the job retries and pays
again. Proposals omit goals, arc, relationships, voice and aliases, so most of the character bible
must be typed by hand. A substring bug lets invalid categories through. First-person narrators get
almost no voice chunks, and HTML uploads become one giant paragraph.

## Outcome

Extraction is a chunked map-reduce that works on a full novel within the free tier (spread across
days if necessary, never failing), proposes rich character entries including aliases and a voice
description, merges duplicates across chunks before the author sees them, and tells the author its
cost before it starts.

## Scope

**In**: `app/parsing/canon_extractor.py`, the extraction handler, `ExtractionRun` progress,
proposal schema, the review UI fields, `Character.aliases`, parser paragraph normalisation, the
voice-indexing heuristics for narrators.
**Out**: OCR for scanned PDFs; extraction of plot threads (possible follow-up).

## Design

1. **Map.** Split `content_text` into windows of ~12k tokens on paragraph boundaries (never
   mid-paragraph; a single over-long paragraph is split on sentences). Each window gets one
   fast-model call returning `{characters:[{name, aliases, role_hint, evidence}], canon_entries:
   [...], style_signals: {...}}` with `response_format={"type":"json_object"}` and a
   `max_tokens` sized to the window. Each completed window is checkpointed (SPEC-08).
2. **Reduce.** Deterministically merge candidates across windows by normalised name and alias
   overlap (honorific-aware, reusing the logic proven in `evals/graders/extraction.py`), counting
   mentions. Then one default-model call per merged character (batched, ~10 characters per call)
   writes the full proposal: `role, description, goals, arc, relationships, personality_traits,
   voice_characteristics, aliases`, citing the windows' evidence. Canon entries merge the same way.
   The synopsis proposal is generated last from the merged canon.
3. **Ranking.** Proposals carry `mentions` and `windows`, and the review UI sorts by them and
   pre-deselects characters seen in one window only (eval-plan E5).
4. **Preflight and cost.** Before enqueue, compute windows and estimated tokens and return them;
   refuse when the estimate exceeds what the user can spend in `JOB_MAX_PAUSE_HOURS`, and offer
   "extract the first N chapters" instead.
5. **Aliases.** Add `characters.aliases JSON` (list of strings). Fidelity (`Canon.principals`,
   `canon_terms`), commit merging and scene casting all honour aliases. This also lets an author
   record "the CEL" as an alias of Edric Thane.
6. **Fixes.** Validate categories against a set, not a string. Proposal `role` uses a closed
   vocabulary plus "other: …".
7. **Parser normalisation.** `DocumentParser` returns paragraphs separated by blank lines for every
   format (HTML block elements, DOCX paragraphs, PDF line-joining heuristics), and records the
   source format on `Source` so evals can treat it as a covariate.
8. **Narrator voice.** When a window's style signals mark first-person narration attributable to a
   character (diary headers, "I" narration in a section headed by the character), voice indexing
   attributes that narration to the character, not only paragraphs containing their name.

## Acceptance

1. A 120k-token stubbed source produces ~10 map calls, a bounded number of reduce calls, and
   proposals with one entry per character even when a name appears in every window. *(unit,
   call-counting stub)*
2. A window whose model response is truncated JSON is retried once with a smaller window and never
   fails the run. *(unit)*
3. "Mina", "Mrs. Harker" and "Madam Mina" merge to one proposal with aliases when the windows'
   evidence links them. *(unit, deterministic merge)*
4. Invalid category `"loc"` is stored as `concept`. *(unit)*
5. The SPEC-06 extraction step's proposal F1 on Dracula and Frankenstein meets or beats its
   baseline, and the ingestion step's grounded fraction for epistolary narrators rises above 0.
6. An HTML upload of a three-paragraph document yields three paragraphs. *(unit)*

## Docs

BRD R4.4 gains the map-reduce and cost-preflight requirement; feature set §2 adds aliases;
eval-audit backlog items 1 and 2 closed.
