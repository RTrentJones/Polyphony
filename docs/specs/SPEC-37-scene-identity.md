# SPEC-37 — Scenes have names, and the editor knows where you are

**Closes**: W4-4 · **Size**: M · **Depends on**: the wave-4 day-0 contracts (`beat_title` on every
generated request, `Chapter.number`, `SceneDetail`).

## Decisions

- **One title rule.** `scene_batch.scene_title(request)`: the request's `beat_title`, else the
  first six words of its `scene_description`. `add_scene_job` applies it, so the single and the
  planned paths name scenes alike. A manual scene keeps the title the author typed. There is no
  title field in the generate modal; the author renames in the editor.
- **Old scenes keep `title = NULL`.** No backfill; "Untitled scene" remains for them.
- **Rename:** `PATCH /books/scenes/{id}` with `{title}` (stripped, 1-500 characters, the column's
  limit) returns `{id, title}`. Titles are **not versioned**: a rename is not prose.
- **One book order.** `books.book_order(query)`: live chapters by position, then scene position;
  unfiled scenes last, newest first. The Scenes tab (`GET /books/{id}/scenes`) and the editor's
  neighbours both use it; there is no second ordering.
- **`GET /scenes/{id}`** adds `title`, `chapter: {id, number, title}` and
  `neighbours: {prev, next}`, the adjacent filed scenes in book order (trashed scenes skipped).
  An unfiled scene has `chapter: null` and `{prev: null, next: null}`.
- **Editor header:** "Ch {chapter.number} · {chapter title} ›" then the title as an input inside
  the `h1`. The title saves on blur or Enter and is not part of the prose's dirty state. POV and
  cast sit on one line under it; the cast line under the text is gone.
- **Previous / Next** go through the unsaved-changes guard (SPEC-16), each disabled at its end of
  the book, and first wait for any pending title save.
- Chapter numbers come from the server (`number`), never `position + 1`.
