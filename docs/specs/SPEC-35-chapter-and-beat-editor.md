# SPEC-35 — Edit the chapter and its planned beats before you generate

**Closes**: W4-2 · **Size**: M · **Depends on**: the wave-4 day-0 contracts (`written_beats`,
`Chapter.number`, `ChapterUpdateData.planned_beats`, `PlannedBeatInput`, `ChapterDetail.written`).

## Outcome

The author changes the plan the product writes from (title, summary, and each beat's title,
summary, cast, setting and tone; add and delete beats) before generating, in one form that opens
from "Edit chapter N" on the chapter row. Beat reorder (Move up/down) was cut by the checkpoint
ruling: a new beat is added at the end, and the order changes only when a beat is deleted.

## Design

- **One form, one Save, one PATCH** (`PATCH /books/chapters/{id}`). The body carries only what
  changed: `title`/`summary` if edited, and `planned_beats` (the whole list, without `cast_ids`)
  only if a beat field or the count changed. A rename sends `{title}`. Nothing changed:
  no request. A 422 saves nothing: the strict-cast 422 marks each beat holding an unknown id with
  the server's message; a validation 422 shows its first `msg`.
- **Cast.** The chips show `cast_ids`, the cast the beat will be written with. Chips the author
  never touched keep following the chapter cast: an untouched beat sends its stored
  `character_ids` (`[]` = the chapter cast), so opening the editor or renaming changes no beat's
  cast. A toggled beat sends the chips it shows.
- **One chip control, one POV lock.** `CastChips` and `chapterPovLock` serve the generate modal
  and the beat editor, which both pass it as `locked`; the chapter's live POV chip cannot be
  removed (R-2).
- **Written beats.** `GET /books/chapters/{id}` returns `written` (`{beat id: scene id}`, live,
  not failed or cancelled scenes, first in chapter order) and each scene's `title`. A written beat
  shows "written" and links to its scene. Editing it does not change the scene: "Edit the scene,
  or delete it and generate again." The row reads "k of n planned scenes written".
- **Scene order:** scenes keep the order they were written in (the chain appends), so a beat
  written again after its scene was deleted lands after the scenes already written; the author
  moves scenes with the scene reorder.
- **Labels** use `chapter.number`: "Edit chapter 1", "Beat 2 of chapter 1, title", "Delete beat 2
  of chapter 1", "Cast of beat 2 of chapter 1".
- Restoring a chapter version (History) reloads the open beat list.
