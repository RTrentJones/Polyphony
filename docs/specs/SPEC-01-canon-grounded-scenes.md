# SPEC-01 — Canon-grounded, cast-by-ID scene generation

**Closes**: F1, F10 (backend), eval-audit backlog item 5 · **Size**: M · **Depends on**: —

## Why

Prose mode is the default scene generator and the only one the free tier allows, and it is
canon-blind. `write_beat_prose` (`app/orchestration/prose.py`) and `plan_scene_beats`
(`app/orchestration/workflow.py`) never see the synopsis, canon entries or style guide, and they
do not fence untrusted content. The scene's cast is a list of free-text names; an unmatched name
silently becomes "(No bible entry — infer a consistent voice.)". The planner's per-beat casting is
thrown away and every beat gets the full cast.

## Outcome

Every scene generation, in either mode, receives the book's whole canon and style guide through
`Canon`, fenced as story material. The cast is a set of character IDs from this book, validated at
the API, and each beat carries its own subset of that cast.

## Scope

**In**: prose workflow, beat planner, `ChapterSceneRequest`, the generate-scene modal's cast
picker, scene `characters` storage.
**Out**: ensemble internals (SPEC-14), outline-derived defaults for the modal (SPEC-10), voice
retrieval changes (SPEC-11).

## Design

1. **Request shape.** Add `character_ids: list[UUID]` to `ChapterSceneRequest`
   (`app/api/books.py:84`). Keep `characters: list[str]` for one release as a deprecated input,
   *(since removed in SPEC-15c: `character_ids` is the only cast input, and `characters` is 422)*
   resolved to IDs by exact name within the book; reject (422) any name or ID that is not in the
   chapter's book, listing the unknowns. Store both IDs and resolved names on the scene
   (`Scene.characters` stays the display list; add `Scene.character_ids` JSON).
2. **Canon in the job.** The handler builds `Canon` once per job with `build_canon(session,
   book_id)` (as the ensemble already does) and passes it down. `book_id` becomes required in
   `run_prose_scene_workflow`.
3. **Prompt structure.** Split the beat prompt into a cached-friendly prefix and a per-beat
   suffix:
   - prefix: `STORY_MATERIAL_NOTICE`, `canon.full_block()` (synopsis, cast, canon entries,
     style), the chapter summary fenced as `<chapter>`;
   - suffix: the beat, the per-beat cast context (fenced as `<character>` blocks), the prior
     tail (fenced as `<prior>`), word target.
   The style guide replaces the request's `pov` default when the request leaves POV blank;
   `style_notes` remains an override and is labelled as such in the prompt.
4. **Planner returns structured beats.** `plan_scene_beats` asks for a JSON array
   `[{"description", "character_ids" | "characters"}]`, given the canon and the scene's cast with
   IDs, and validates each beat's cast to a subset of the scene's cast (unknown names dropped with
   a logged warning, empty subset falls back to the full scene cast). The numbered-line parser
   stays as the fallback when JSON parsing fails.
5. **Unmatched names cannot reach the prompt.** `build_cast_context` takes `Character` rows, not
   names; the "(No bible entry…)" branch is removed because validation makes it unreachable.
6. **Frontend.** The modal's picker lists `book.characters` (the book's cast from the Canon
   store), not sources. Free-text entry is removed; "New character…" opens the canon editor.
7. **Budget.** Before enqueue, estimate `(1 + beats_estimate) × tokens(canon + cast context)` and
   preflight as in `generate_plan`; return the estimate in the response (SPEC-12 renders it).

Canon size per call grows from a few hundred tokens to the whole canon (a 20k-char synopsis is
~5k tokens). At 4–5 calls per scene that is 25–30k input tokens, inside the 200k budget but large
enough that the estimate in step 7 matters. Measure it in the PR description against the `cel`
fixture.

## Acceptance

1. With a stubbed LLM client, the assembled beat prompt for the `cel` fixture book contains the
   synopsis tail (the gloss "Chief Executive Lich" at char ~10,093), every canon entry name, and
   the style guide's POV and tense. *(unit, no LLM)*
2. Every piece of author or upload content in the beat and planner prompts sits inside an
   `as_quoted_block` fence and the prompt opens with `STORY_MATERIAL_NOTICE`. *(unit: assert on
   the assembled prompt)*
3. `POST /chapters/{id}/scenes/generate` with a name or ID not in the chapter's book returns 422
   naming the unknowns, and creates no scene and no job. *(unit/API)*
4. A planner response assigning two of three characters to beat 1 produces a beat-1 cast context
   with exactly those two. *(unit)*
5. No string slice (`[:N]`) of author content remains in `app/orchestration/prose.py`,
   `app/orchestration/workflow.py` or `app/characters/context.py`. *(grep check in a unit test,
   mirroring `test_import_safety.py`)*
6. The SPEC-06 prose eval step passes the book's cast and canon and continues to score at or
   above its pre-change baseline within the noise band.

## Docs

BRD R1.1: add "scene generation (prose and ensemble)" to the explicit list. Feature set §3:
"Scene prose generation" note changes from "canon-blind" to shipped. Skill: add prose to "anything
that feeds an LLM".
