# SPEC-32 — Chapter rows are unambiguous, and Generate is never a dead end

**Closes**: W3-6 (N8, N9; B12 rest) and the coordinator's scope addition (the server publishes
each beat's resolved cast) · review S8, R-2 · **Size**: M · **Depends on**: SPEC-23 (planned
beats), SPEC-24 (`beat_people`).

## Why

- Two chapters titled "Paperwork" gave two rows whose controls a screen reader announced alike
  ("Delete Paperwork"), and the e2e journey could not tell them apart either.
- "Generate scene" opened a form that could not succeed in a book with no chapters or no
  characters.
- The scene picker's chips and estimate could omit people the server added: a beat's cast was
  resolved on the server only when the modal sent no cast (`serverCast`), and the chapter POV was
  added to an explicit batch cast but not to an explicit single-scene cast.

## Outcome

Every row and button says exactly what it will do, and the modal sends exactly the cast it shows.

## Design

### Rows (`ChaptersSection.tsx`)

- One `rowLabel(chapter)` = `chapter {position + 1}, {displayed title}`, the number the row shows.
  The Expand/Collapse, Move up/down, History, Delete and "More actions" labels use it: "Delete
  chapter 3, Paperwork".
- Rows carry `data-chapter-id` and `data-chapter-position`; `data-chapter-title` is deleted.
  `scripts/e2e_browser.py` locates rows by id (`chapter_row(page, id)`) or position, and reads the
  stored title from the row's title element only where the Trash names it.

### Disabled Generate

- `generateBlocker(book)` (in `GenerateSceneModal.tsx`): "Add a chapter first" when the book has
  no chapters, "Add characters to cast a scene" when `book.character_count === 0`, else null.
- The panels call `generateBlocker(book)` on the loaded book. The paths that change the cast count
  call `refreshBook()`: Canon's character add, edit and delete, and Outline's "add a suggested
  character"; an extraction commit returns to the book page, which loads the book afresh.
- The reason is visible text (`id="generate-blocker"`), and every blocked control is `disabled`
  with `aria-describedby` pointing at it: the Scenes tab's Generate, each row's Generate, "Generate
  all planned scenes" in the row and in the phone menu.
- The Scenes tab's "Create a chapter first" toast is deleted.

### One cast, server-derived (`app/api/books.py`)

- `book_cast_index(db, book_id)` is the one live-cast load; `chapter_people(chapter, index)`
  returns the chapter's live cast ids and live POV id (pure). They replace `beat_cast_context`.
- `_chapter_dict(chapter, index)` takes the index as a required parameter. Each planned beat gains
  `cast_ids = beat_people(beat, index, *chapter_people(chapter, index))[0]`: derived on read, never
  stored, so a secondary added to the cast after promote is in her beat's `cast_ids` while the
  stored `character_ids` are unchanged. Every chapter response (book GET, chapter GET, create,
  update, move) loads the index once per request.
- `GET /books/{id}` gains `character_count` (live characters). `BookProvider` loads characters
  lazily, so an empty client list cannot mean "none".
- `with_pov(ids, pov_id)`: an explicit cast with the chapter's live POV first. It is the one
  explicit-cast rule for `POST …/scenes/generate` and `…/scenes/generate-planned`; an empty
  cast stays empty (`resolve_cast` refuses it). Both endpoints fill `pov_character` from the
  chapter when the request has none.
- A single scene from a beat still stores `planned_beat_id` and `beat_people(...)[1]` as
  `secondary_names`. The "no explicit cast → cast the beat" branch of the single-scene endpoint is
  deleted: the modal always sends ids.

### The modal sends what it shows (`GenerateSceneModal.tsx`)

- Picking a beat sets `selected` to `beat.cast_ids`. `serverCast`, `castFromBeat` and
  `pickBeat`'s chapter-cast fallback are deleted; the request always carries `selected`, and
  `EstimateHint` estimates from it, so chips, estimate and request are one value.
- **Locked POV chip (R-2).** The chapter's live POV character is in every scene of the chapter
  (`with_pov`), so its chip is preselected, cannot be removed (`toggle` ignores it), is
  `aria-disabled`, and shows "point of view for this chapter". There is no client copy of
  `with_pov`.

## Tests

`test_planned_beats.py`: Prava's beat `cast_ids` after she joins the cast (stored ids unchanged)
and `character_count`; a single scene and a batch with the same explicit ids get the same cast,
POV first. Vitest: distinct labels for two "Paperwork" rows; Generate disabled with each reason, and
enabled once the first character is added in Canon (`refreshBook`);
a picked beat's `cast_ids` are the chips, the estimate and the request; the POV chip cannot be
removed and is sent (R-2). Journey: an empty book shows Generate disabled with its reason; two
same-title chapters, delete the second by its label, the first remains.

## Docs delta (for the orchestrator)

- `docs/feature-set.md`, Chapters: "Each row's controls name the chapter's number and title
  ('Delete chapter 3, Paperwork'). Generate is disabled, with the reason shown, until the book has a
  chapter and characters. Picking a planned beat shows the cast the scene will be written with; the
  chapter's point-of-view character is always included and cannot be removed."
- `docs/BRD.md` (R7, generation UI): the scene modal sends exactly the cast it shows.
