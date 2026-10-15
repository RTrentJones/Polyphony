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
`{node_id, title, summary, character_ids, secondary_names, setting, tone}`. It is always read
through `normalize_planned_beats`, which never raises. `character_ids == []` means "use the chapter
cast". API chapter dicts (`_chapter_dict`) carry `planned_beats` (`[]` for a hand-made chapter).

### Promote (`app/api/plans.py::_promote_node`, `promoted_beats`)

For each child of the node, one beat:

- `node_id`, `title`, `summary`, `setting`, `tone` are the child's.
- `character_ids` are the child's `character_ids` (or, defensively, `characters` names) that resolve
  in the book's **current** cast (`CastIndex`). A character removed since the outline was drafted
  is dropped.
- A `secondary_names` entry that now resolves to the cast (the author added that person) **moves**
  into `character_ids`. The others stay in `secondary_names`.

The chapter `summary` is the node summary only. The chapter `title` is
`clean_chapter_title(node.title)`, or `Chapter {position+1}` when that is empty.

### Generate-planned (`POST /books/chapters/{id}/scenes/generate-planned`)

- **Beats**: `scene_batch.planned_beats(chapter)`. It returns `chapter.planned_beats` when that
  holds any. Otherwise it parses the legacy summary lines (`planned_beat_lines`, the old string
  parser, renamed) into the same shape: split on the first `": "`, and `title:` or a bare line is a
  title with an empty summary. So an un-migrated or hand-written chapter still generates.
- **Already written**: `unwritten_beats` matches each beat's `beat_scene_description(beat)`
  (`"title: summary"`, byte-identical to the legacy line) against the live scenes' descriptions. A
  trailing `:` is ignored on both sides, so a legacy `Lunch:` scene and a bare `Lunch` line still
  count. Scenes written before this spec still count as written.
- **Cast precedence**, per beat: a non-empty `payload.character_ids` (an explicit override for
  every beat; an unknown id is a 422, SPEC-01) > the beat's `character_ids` plus any
  `secondary_names` entry that now resolves > the chapter's `character_ids`. In every case ids that
  are not live are dropped and the chapter POV character, when live, is put first if missing. A beat
  whose cast ends up empty is a 422 (the same as before). `resolve_cast` runs once per distinct
  cast.
- **Request** (the keys the scene chain carries; SPEC-24 reads them):
  `scene_description = beat_scene_description(beat)`;
  `setting = payload.setting or beat.setting`; `emotional_tone = payload.emotional_tone or
  beat.tone` (both may be `""`); `pov_character` = the chapter POV name or `None`;
  `planned_beat_id = beat.node_id`; `secondary_names` = the beat's names that still do not
  resolve (may be `[]`). `source_id`, `target_word_count`, `style_notes` and `mode` are unchanged.
- `PlannedScenesRequest.emotional_tone` defaults to `""` (was `"neutral"`): the style guide's tone
  governs.
- **Preflight**: the sum of the single-scene estimator over the scenes to write, each with its own
  cast size and cast context.

### Chapter PATCH (`PATCH /books/chapters/{id}`)

`planned_beats: Optional[list[PlannedBeat]]` (at most 12) replaces the stored list. It is
**strict**: any character id that is not a live character of this book is a 422 naming the unknowns
(the same body shape as `resolve_cast`). The list is stored normalized. A changed list versions the
chapter (SPEC-13) like a title or summary edit.

### Versions (`app/versioning/entities.py`)

`CHAPTER_FIELDS` includes `planned_beats` (normalized in the version content). Restore writes it
back through `apply_chapter_cast`, normalized, with ids no longer live dropped from each beat. A
version taken before this spec has no `planned_beats` key and leaves the live beats alone.

### Continuity fact sheet (`build_fact_sheet`)

Each chapter is a heading `Ch N: title`, then its summary indented, then its beats as indented
`  - title: summary` lines. A beat list never contains a `- Ch` line. A legacy chapter's
`Planned beats:` text is rendered the same way, from the fallback parse.

### `suggested_characters` (wave-2 contract §1.2)

`[{name, synopsis_sentence}]`, always a list. SPEC-24 computes it
(`secondaries.suggested_characters(nodes, canon)`); this spec exposes it:

- `GET /books/{id}/plans`: each plan dict has `suggested_characters`. It is computed only for
  `kind == "outline"` and `status == "ready"`, against the current cast, so a name disappears once
  the author adds that person. One `build_canon` per request, and none when no plan qualifies.
- `POST .../plans/promote`: a top-level key computed over the promoted node.
  `POST .../plans/promote-all`: a top-level key computed over the whole outline.
- An exception in the suggestion code is logged and yields `[]`. Suggestions are advisory and never
  take the plans or a promote down.

### Migration `0019_w2_planned_beats` (`w2_planned_beats`, after `b3_scene_revise`)

Postgres only, and guarded like 0017/0018. Migrations do not import app code, so the beat
normalization and the `clean_chapter_title` regex are copied inline.

- `upgrade`:
  1. `ADD COLUMN IF NOT EXISTS planned_beats JSONB`.
  2. For each chapter whose summary contains `Planned beats:` (and has no beats yet): if
     `plan_node_id` matches a node of the book's outline, the beats are that node's children, with
     node_ids and any new fields kept. Otherwise the `- title: summary` lines are parsed, with new
     uuid4 node_ids. The summary becomes the text before the marker, stripped.
  3. For chapters with a `plan_node_id`, a leading `Chapter N:` (same regex as
     `clean_chapter_title`) is stripped from the title, unless that would leave it empty.
- `downgrade`: re-appends `\n\nPlanned beats:\n- title: summary…` to the summary of every chapter
  with beats, then drops the column. **The title strip is not reversed**: the prefix is not
  recoverable, and older code does not need it.
- upgrade → downgrade → upgrade leaves the same summaries, titles and beat titles/summaries, and a
  plan-backed chapter keeps its node_ids.

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
| A pre-migration chapter generates through the fallback, and its old-format scenes count as written | `test_a_summary_only_chapter_still_generates`, `test_legacy_lines_parse_into_beats_that_rebuild_the_line`, `test_job_enqueue.py` (summary-only chapter) |
| The fact sheet has indented beats and no `- Ch` in a beat list | `test_continuity_v2.py::test_fact_sheet_indents_planned_beats_under_their_chapter` |
| `GET /plans` and both promotes return `suggested_characters`; only ready outlines; an error yields `[]` | `test_plans_and_promote_return_suggested_characters`, `test_suggestions_only_for_a_ready_outline_and_never_fatal` |
| PG: JSONB column, backfill from plan children keeps node_ids, parse fallback, title strip, round trip | `tests/integration/test_pg_planned_beats.py` |

## Docs (delta for the orchestrator to apply)

**BRD §5.x (generation)**, a new requirement after R1.1a:

> **R1.1b** **Planned beats are data** (SPEC-23). A promoted chapter stores its outline node's
> children as `planned_beats` (`{node_id, title, summary, character_ids, secondary_names, setting,
> tone}`), not as text in its summary. "Generate all planned scenes" writes each scene from its
> beat: the beat's cast (else the chapter's; an explicit request cast overrides every beat) plus the
> chapter's POV character, and the beat's setting and tone. An empty tone stays empty; the style
> guide governs, and "neutral" is never injected. Editing the summary does not change what is
> generated. The chapter PATCH edits the beats strictly (an id outside the book's live cast is a
> 422) and versions them. Promoted titles drop the outline's "Chapter N:" prefix.

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
  (migration `0019_w2_planned_beats` backfills from the summary text or the outline node);
  `PATCH /books/chapters/{id}` `planned_beats` (strict, versioned); editable list in the UI: W2-7
  (SPEC-23) |".
