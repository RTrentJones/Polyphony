# SPEC-35 — Edit the chapter and its planned beats before you generate

**Closes**: W4-2 · **Size**: M · **Depends on**: the wave-4 day-0 contracts (`written_beats`,
`Chapter.number`, `ChapterUpdateData.planned_beats`, `PlannedBeatInput`, `ChapterDetail.written`).

## Outcome

The author changes the plan the product writes from (title, summary, and each beat's title,
summary, cast, setting and tone; add, delete, reorder beats) before generating, in one form.

## Design

- **One form, one Save, one PATCH** (`PATCH /books/chapters/{id}`). The body carries only what
  changed: `title`/`summary` if edited, and `planned_beats` (the whole list, without `cast_ids`)
  only if a beat field, the order or the count changed. A rename sends `{title}`. Nothing changed:
  no request. A 422 saves nothing: the strict-cast 422 marks each beat holding an unknown id with
  the server's message; a validation 422 shows its first `msg`.
- **Cast.** The chips show `cast_ids`, the cast the beat will be written with. Chips the author
  never touched keep following the chapter cast: an untouched beat sends its stored
  `character_ids` (`[]` = the chapter cast), so opening the editor or renaming changes no beat's
  cast. A toggled beat sends the chips it shows.
- **One chip control, one POV lock.** `CastChips` and `chapterPovLock` serve the generate modal
  and the beat editor; the chapter's live POV chip cannot be removed (R-2).
- **Written beats.** `GET /books/chapters/{id}` returns `written` (`{beat id: scene id}`, live,
  not failed or cancelled scenes, first in chapter order) and each scene's `title`. A written beat
  shows "written" and links to its scene. Editing it does not change the scene: "Edit the scene,
  or delete it and generate again." The row reads "k of n planned scenes written".
- **Reorder and insert.** Scenes keep the order they were written in: the chain appends each new
  scene at the end of the chapter. A beat inserted (or moved) before a written one gets its scene
  after it; the author moves scenes with the existing scene reorder. There is no "insert the scene
  at its beat's place" rule. An edit while a batch is in flight applies to unqueued beats only.
- **Labels** use `chapter.number`: "Edit chapter 1", "Beat 2 of chapter 1, title", "Move beat 2 of
  chapter 1 up", "Delete beat 2 of chapter 1", "Cast of beat 2 of chapter 1".
- Restoring a chapter version (History) reloads the open beat list.
