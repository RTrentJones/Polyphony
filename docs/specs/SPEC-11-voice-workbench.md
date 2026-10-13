# SPEC-11 — Voice workbench and grounding provenance

**Closes**: F15 (R5.2, R5.3), F18 · **Size**: M · **Depends on**: SPEC-01 (cast by ID)

## Why

Voice is the product's namesake, and the author cannot see it. The chunk list, chunk edit and
delete, retrieval inspector, voice-sample seeding and test-dialogue endpoints all exist
(`app/api/characters.py:283`–`:424`) and have no UI. R5.3 also asks which chunks grounded a
*particular* generation, and scenes record nothing about their grounding. Meanwhile
`character_chunks` is written alongside `voice_chunks`, never read, and drifts after every edit.

## Outcome

Each character in the Canon tab has a Voice section where the author can browse, edit, delete and
add samples, run a retrieval query with scores, and test a line of dialogue. Each generated scene
records, per beat, which chunks grounded each character and at what similarity, and the scene view
can show it.

## Scope

**In**: frontend Voice section, a grounding record on scenes, retiring `character_chunks`.
**Out**: chunk versioning (BRD §8 keeps it out of scope); re-chunking strategies (eval plan V5).

## Design

1. **Voice section** (in the character card of `CanonPanel`, or a `VoicePanel` component): a
   paginated chunk list (type, source, text, word count) with inline edit (re-embeds, via the
   existing PATCH) and delete; an "Add samples" textarea (one sample per paragraph) using the
   existing endpoint; "Test retrieval" (query → ranked chunks with scores and the threshold line
   drawn); "Test a line" (prompt → generated line, showing the chunks used).
2. **Chunk list API.** Add `skip/limit` and `source_id`/`chunk_type` filters to
   `GET /characters/{id}/chunks`; include each chunk's `id`, `source_id` and `created_at`.
3. **Grounding record.** `retrieve_similar` returns chunk `id`s. The prose and ensemble workflows
   collect `{beat_index: {character_id: [{chunk_id, score}]}}` and store it on
   `Scene.evaluation_scores["grounding"]` (no migration; the column is already a JSON metadata
   bucket). `GET /books/scenes/{id}/grounding` resolves IDs to current chunk text, marking deleted
   chunks as such. Each entry also stores a bounded (~300-char) `excerpt` of the sample's text at
   generation time, so a deleted chunk still shows its wording, marked "deleted since generation".
   A character no sample grounded — none indexed, or an ensemble context-only character — gets an
   entry with no chunk and a `reason`, so the view lists every character in the beat.
4. **Scene view.** A "Why does it sound like this?" disclosure under each scene lists, per
   character, the grounding samples with scores.
5. **Retire `character_chunks`.** Stop writing it in `add_voice_samples` and the indexing
   pipeline; migration drops the table. Confirm nothing reads it with a grep in the PR.

## Acceptance

1. Editing a chunk in the UI changes its text and its retrieval score for a fixed query.
   *(backend unit for the API; manual UI check recorded in the PR until frontend tests exist)*
2. A generated scene stores grounding for every beat and every character in that beat's cast.
   *(unit, stubbed store and client)*
3. The grounding endpoint returns current text for live chunks and `deleted: true` for removed ones.
   *(unit)*
4. No code references `CharacterChunk` after the migration. *(grep check)*

## Docs

Feature set §6 rows for chunk browser, retrieval inspector and the `character_chunks` mirror
updated; BRD R5.3 unchanged (now satisfied).
