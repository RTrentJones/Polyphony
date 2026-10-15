# SPEC-23 — Planned beats as data

**Closes**: backlog W2-1 (backend half), B9 (promote title), B7 (backend) · **Size**: L ·
**Depends on**: SPEC-10, SPEC-12, SPEC-13 · **Pairs with**: SPEC-24 (the outline produces the beat
fields), SPEC-25 (the frontend reads `planned_beats`)

## Why

Promote wrote an outline node's children into the chapter summary as `Planned beats:` lines.
"Generate all planned scenes" re-parsed that text and wrote every scene with the whole chapter
cast, the request's setting (usually blank) and the tone `"neutral"`. The intake scene with Milo and
a caseworker became a Milo-and-Zara scene, and a dread-filled scene was flattened to "neutral".
Editing the chapter summary silently changed what got generated. Promoted titles kept the
outline's "Chapter 2:" prefix, so the export printed "Chapter 2: Chapter 2: Milo Voss".

## Outcome

A promoted chapter stores its beats as data in `chapters.planned_beats`. Generate-planned writes
each scene with that beat's own cast, setting and tone, plus the chapter's POV character. The
summary is the chapter's own text, and editing it changes nothing that gets generated. Promoted
titles lose their "Chapter N:" prefix.

## Scope

**In**: the column and migration `0019_w2_planned_beats`; the promote mapping; per-beat
generate-planned requests; the chapter PATCH for `planned_beats`; chapter versions carry the beats;
the continuity fact sheet; `suggested_characters` on `GET /plans` and both promote routes.
**Out**: the outline prompts and validator that produce per-beat `characters` / `secondary_names` /
`setting` / `tone` (SPEC-24), and computing `suggested_characters` (SPEC-24,
`app/planning/secondaries.py`); the frontend read path (SPEC-25); an editable beat list in the UI
(W2-7).

## Design

### Storage

`chapters.planned_beats`: JSON on sqlite, JSONB on Postgres, nullable. It holds
`list[PlannedBeat]` (`app/planning/planned_beat.py`, the frozen wave-2 contract):
`{node_id, title, summary, character_ids, secondary_names, setting, tone}`. Every reader
(`_chapter_dict`, generate-planned, `list_books`'s planned count, the fact sheet) calls
`normalize_planned_beats(chapter.planned_beats)`, which never raises. `character_ids == []` means
"use the chapter cast". API chapter dicts carry `planned_beats` (`[]` for a hand-made chapter).

**There is no runtime fallback** (wave 2.5, PR-B1). Migrations run at container start, before the
app serves, so after 0019 no served request meets a legacy-format chapter. 0019 and the chapter
restore path convert legacy "Planned beats:" blocks at write time. A chapter whose `planned_beats`
is empty plans **0** scenes, whatever its summary says: lines an author types into a summary are
text.

### Promote (`app/api/plans.py::_promote_node`, `promoted_beats`)

For each child of the node, one beat:

- `node_id`, `title`, `summary`, `setting`, `tone` are the child's.
- One `classify_people(child.character_ids + child.characters + child.secondary_names, current
  cast, None)` call (SPEC-24) gives the beat's `character_ids` (`.ids`) and `secondary_names`
  (`.secondary`). A character removed since the outline was drafted is dropped; a secondary name
  that now resolves (the author added that person) **moves** into `character_ids`. A stored
  outline's legacy `characters` key counts the same as `character_ids` + `secondary_names`.
- The chapter's `character_ids` are `classify_people(node.character_ids, cast, None).ids`, with the
  POV character first.

The chapter `summary` is the node summary only. The chapter `title` is
`clean_chapter_title(node.title)`, or `Chapter {position+1}` when that is empty.

### Generate-planned (`POST /books/chapters/{id}/scenes/generate-planned`)

- **Beats**: `normalize_planned_beats(chapter.planned_beats)`. Empty → 422 "no planned beats".
- **Already written**: by **id only** (wave 2.5, review B3). `scene_batch.written_beat_ids` reads
  the `scene_request.planned_beat_id` of the chapter's live, not failed/cancelled scenes, and
  `planned_beat.unwritten_beats(beats, written_ids)` keeps the beats whose `node_id` is not among
  them. Renaming or re-summarising a beat never makes its scene unwritten. Scenes written before
  this spec were linked to their beats once, by migration 0019.
- **Cast precedence**, per beat: a non-empty `payload.character_ids` (an explicit override for
  every beat, **not** filtered: an unknown id is a 422, SPEC-01) > the beat's own cast,
  `classify_people(beat.character_ids + beat.secondary_names, live cast, None).ids` (dead ids
  dropped; a secondary that now resolves is cast) > the chapter's `character_ids` that are still
  live. A beat whose own cast was all trashed therefore falls back to the chapter's (wave 2.5,
  review S8). The chapter POV character, when live, is put first if missing. `resolve_cast` runs
  once per distinct cast; it 422s an unknown explicit id and an empty cast.
- **Request** (the keys the scene chain carries; SPEC-24 reads them):
  `scene_description = beat_scene_description(beat)`;
  `setting = payload.setting or beat.setting`; `emotional_tone = payload.emotional_tone or
  beat.tone` (both may be `""`); `pov_character` = the chapter POV name or `None`;
  `planned_beat_id = beat.node_id`; `secondary_names` = the same `classify_people` call's
  `.secondary` (may be `[]`). `source_id`, `target_word_count`, `style_notes` and `mode` are unchanged.
- `PlannedScenesRequest.emotional_tone` defaults to `""` (was `"neutral"`): the style guide's tone
  governs.
- **Preflight**: the sum of the single-scene estimator over the scenes to write, each with its own
  cast size and cast context.

### A single scene from a beat (`POST /books/chapters/{id}/scenes/generate`)

`ChapterSceneRequest.planned_beat_id: Optional[str]` (wave 2.5, review S7; the modal sends it).
When it names a beat of the chapter's `planned_beats`, the stored request carries
`planned_beat_id` (so generate-planned counts that beat as written) and `secondary_names` =
`classify_people(beat.secondary_names, live cast, None).secondary`. An id the chapter does not
have (a stale modal) is stored as `None`. The cast, setting and tone are the request's own.

### Chapter PATCH (`PATCH /books/chapters/{id}`)

`planned_beats: Optional[list[PlannedBeat]]` (at most 12) replaces the stored list. It is
**strict**: any character id that is not a live character of this book is a 422 naming the unknowns
(the same body shape as `resolve_cast`). The list is stored normalized. A changed list versions the
chapter (SPEC-13) like a title or summary edit.

### Versions (`app/versioning/entities.py`)

`CHAPTER_FIELDS` includes `planned_beats` (normalized in the version content). Restore writes it
back through `apply_chapter_cast`, normalized, with ids no longer live dropped from each beat.

A version taken before this spec has no `planned_beats` key, and its summary may hold a
promote-format "Planned beats:" block. Restore (`app/api/versioning.py`) converts it at that write
with `split_legacy_summary`: the summary becomes the text without the block, and **when the live
`planned_beats` is empty** the lines become the beats (`beat_from_line`, uuid4 ids, stored once).
Otherwise the live beats stay, so restoring the same version twice adds nothing.
`split_legacy_summary` / `beat_from_line` exist only for this path and 0019's parity test. They go
once no stored chapter version predates SPEC-23; versions are kept forever today, so they stay
until a retention policy exists.

### Continuity fact sheet (`build_fact_sheet`)

Each chapter is a heading `Ch N: title`, then its summary indented, then its beats as indented
`  - title: summary` lines. A beat list never contains a `- Ch` line. `app/planning` does not
import `app.jobs` (a one-assertion layering test).

### `suggested_characters` (wave-2 contract §1.2)

`[{name, synopsis_sentence}]`, always a list. SPEC-24 computes it
(`secondaries.suggested_characters(names, canon)`); this spec collects the names and exposes it.
The names are a node's `secondary_names` plus, for a **promoted** node, its chapter's stored
beats' `secondary_names` (what generation uses), else the outline children's. `GET /plans` reads
the book's live promoted chapters once per request.

- `GET /books/{id}/plans`: each plan dict has `suggested_characters`. It is computed only for
  `kind == "outline"` and `status == "ready"`, against the current cast, so a name disappears once
  the author adds that person. One `build_canon` per request, and none when no plan qualifies.
- `POST .../plans/promote`: a top-level key computed over the promoted node.
  `POST .../plans/promote-all`: a top-level key computed over the whole outline.
- A `ValueError`, `TypeError` or `KeyError` in the suggestion code is logged and yields `[]`.
  Suggestions are advisory and never take the plans or a promote down; any other exception is a
  bug and surfaces.

### Migration `0019_w2_planned_beats` (`w2_planned_beats`, after `b3_scene_revise`)

Postgres only, and guarded like 0017/0018. Migrations do not import app code, so the block parser
(`split_legacy_summary`), the beat caps and the `clean_chapter_title` regex are copied inline; a
parity test checks the copy. Wave 2 never reached production, so wave 2.5 edited this migration in
place.

- `upgrade`:
  1. `ADD COLUMN IF NOT EXISTS planned_beats JSONB`.
  2. For each chapter with `planned_beats IS NULL` whose summary holds a **promote-format** block
     (`Planned beats:` alone on its line, at the start of the summary or after a blank line, and a
     `- ` line first): each `- ` line, with any continuation lines folded into it, becomes a beat
     (`beat_from_line`, a new uuid4 `node_id`, no cast, setting or tone), capped at 12. **The lines
     are the truth**: the author may have edited them, so the outline is never read. The summary
     keeps its text before the block and after the block's ending blank line. A summary that merely
     mentions the phrase ("Planned beats: TBD. Then…") is left alone and counted as skipped.
  3. **Scene linking**, once: each converted chapter's live, not failed/cancelled scenes with no
     `scene_request.planned_beat_id`, in position order, are matched to the beats by occurrence on
     the `- ` line (a trailing `:` ignored on both sides): each scene takes the first open beat
     with its line, and each beat takes at most one scene. The match writes
     `scene_request.planned_beat_id`, an additive key older code ignores.
  4. For chapters with a `plan_node_id`, a leading `Chapter N:` (same regex as
     `clean_chapter_title`) is stripped from the title, unless that would leave it empty.
  5. One log line on `alembic.runtime.migration`:
     `0019 planned beats: converted=<n> skipped=<n> scenes_linked=<n> beats=<n>`.
- `downgrade`: re-appends `\n\nPlanned beats:\n- title: summary…` to the summary of every chapter
  with beats, then drops the column. Scenes keep `planned_beat_id`. **The title strip is not
  reversed**: the prefix is not recoverable, and older code does not need it.
- The round trip is exact for promote-format rows. Author text after the block ends up before it,
  and continuation lines come back joined into their beat's line.

## Acceptance

| Criterion | Test |
|---|---|
| Promote writes `planned_beats` with the children's node_ids, cast, setting and tone; the summary has no "Planned beats:"; "Chapter 2: Milo Voss" is stored as "Milo Voss" | `test_planned_beats.py::test_promote_writes_beats_as_data`, `test_outline_handoff.py::test_ac3_…`, `test_books_api.py::test_upsert_and_promote_outline` |
| A secondary name that now resolves gets that character's id (moved, not duplicated); a dead id is dropped | `test_promote_moves_a_secondary_that_now_resolves_into_the_cast` |
| A title that is only a number falls back to "Chapter N" | `test_an_all_number_title_falls_back_to_the_position` |
| Each request's cast is its beat's plus the POV; an empty beat cast falls back to the chapter's; setting and tone come from the beat; an empty tone stays `""`; `planned_beat_id` and `secondary_names` are passed | `test_each_planned_scene_takes_its_beats_cast_setting_and_tone` |
| An explicit `character_ids` overrides every beat | `test_an_explicit_cast_overrides_every_beat` |
| A secondary added to the cast since promote is cast | `test_a_secondary_added_to_the_cast_since_promote_is_cast` |
| The preflight sums per-beat cast sizes | `test_the_preflight_sums_each_beats_cast`, `test_handoff_followups.py::test_generate_planned_is_preflighted` |
| Editing the summary leaves the requests unchanged | `test_editing_the_summary_does_not_change_the_requests` |
| PATCH with a foreign id is a 422; a beat edit is versioned and restorable; restore drops dead ids | `test_patch_planned_beats_is_strict_and_versioned`, `test_restoring_beats_drops_characters_no_longer_live` |
| A scene counts as written by its beat's id: renaming a beat does not write it again | `test_a_renamed_beat_keeps_its_written_scene`, `test_planned_beat_contract.py::test_unwritten_beats_is_by_id_only` |
| No runtime fallback: a typed block in an empty-beats chapter plans nothing (422; `list_books` counts 0) | `test_a_typed_block_in_the_summary_plans_nothing`, `test_list_books_progress.py::test_planned_count_reads_the_beats_only` |
| Restoring a pre-SPEC-23 version moves its block into `planned_beats` once | `test_restoring_a_pre_spec23_version_converts_its_block_once` |
| A beat whose own cast was trashed takes the chapter cast; an explicit unknown id still 422s | `test_a_trashed_beat_cast_falls_back_but_an_explicit_unknown_422s` |
| A single scene from a beat stores its id and secondaries; a stale id is stored as `None` | `test_a_single_scene_from_a_beat_stores_its_id_and_secondaries` |
| Legacy `characters` and `character_ids` + `secondary_names` promote the same beats; a trashed node cast is dropped | `test_legacy_characters_and_people_lists_promote_the_same_beats` |
| The fact sheet has indented beats and no `- Ch` in a beat list | `test_continuity_v2.py::test_fact_sheet_indents_planned_beats_under_their_chapter` |
| `GET /plans` and both promotes return `suggested_characters`; only ready outlines; an expected error yields `[]`; a promoted node's names come from its chapter's beats | `test_plans_and_promote_return_suggested_characters`, `test_suggestions_only_for_a_ready_outline_and_never_fatal`, `test_a_promoted_nodes_suggestions_come_from_its_chapter_beats` |
| `app/planning` never imports `app.jobs` | `test_continuity_v2.py::test_planning_never_imports_jobs` |
| PG: JSONB column; the author's edited line kept (i); a stray mention skipped (iii); a block at offset 0 (iv); text after the block kept (v); round trip (vi); parity with the app parser (vii); a pre-SPEC-23 scene linked and skipped by generate-planned (viii); a continuation line stays in its beat (ix); the log line; title strip | `tests/integration/test_pg_planned_beats.py` |

## Docs (delta for the orchestrator to apply)

**BRD §5.x (generation)**, a new requirement after R1.1a:

> **R1.1b** **Planned beats are data** (SPEC-23). A promoted chapter stores its outline node's
> children as `planned_beats` (`{node_id, title, summary, character_ids, secondary_names, setting,
> tone}`), not as text in its summary. "Generate all planned scenes" writes each scene from its
> beat: the beat's cast (else the chapter's; an explicit request cast overrides every beat) plus the
> chapter's POV character, and the beat's setting and tone. An empty tone stays empty; the style
> guide governs, and "neutral" is never injected. Editing the summary does not change what is
> generated, and a "Planned beats:" block typed into a summary plans nothing. A scene is linked
> to its beat by id, so renaming a beat never writes its scene twice. The chapter PATCH edits the
> beats strictly (an id outside the book's live cast is a 422) and versions them. Promoted titles
> drop the outline's "Chapter N:" prefix.

**feature-set.md**:

- Row "Promote outline node → chapter": append "; the node's children become the chapter's
  `planned_beats` (data: cast, setting, tone, named secondaries), the title loses its 'Chapter N:'
  prefix, and the response carries `suggested_characters` (SPEC-23)".
- Row "Generate all planned scenes of a chapter": replace "with the chapter's cast/POV" with "each
  from its planned beat (the beat's cast plus the chapter POV, else the chapter cast; the beat's
  setting and tone; `planned_beat_id` and unresolved `secondary_names` on the request)" and append
  "(SPEC-23)".
- Row `entity_versions`: append "; a chapter version also carries its planned beats (SPEC-23)".
- New row under planning: "| Planned beats as data | R1.1b | ✅ (API) | `chapters.planned_beats`
  (migration `0019_w2_planned_beats` converts the summary's promote-format lines and links the
  scenes already written to their beats);
  `PATCH /books/chapters/{id}` `planned_beats` (strict, versioned); editable list in the UI: W2-7
  (SPEC-23) |".
