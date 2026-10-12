# SPEC-04 — Scenes are book-rooted

**Closes**: F6 · **Size**: M · **Depends on**: —

## Why

`Scene` reaches its book only through a nullable `chapter_id` with `ON DELETE SET NULL`, and the
ORM relationship has no cascade. Deleting a chapter or a book leaves scenes, beats and revisions
as user-scoped orphans that no book view can reach, while `GET /api/v1/scenes/` still lists them.
That violates invariant 2 and the BRD §4.1 tree. Scenes are now generable only inside a chapter
(PR #28), so there is no remaining use for a chapter-less, book-less scene.

## Outcome

Every scene has a non-null `book_id` with `ON DELETE CASCADE`. Deleting a book deletes its scenes.
Deleting a chapter does not silently orphan prose: the author chooses between moving its scenes to
another chapter and deleting them, and the deletion is recoverable under SPEC-13.

## Scope

**In**: `Scene` model and migration, scene endpoints, chapter delete, `list_book_scenes`, the
user-level `/api/v1/scenes` router.
**Out**: scene versioning beyond `scene_revisions` (unchanged by design, ADR-002 §5).

## Design

1. **Migration `0009_scene_book_id`.** Add `scenes.book_id UUID`. Backfill from
   `chapters.book_id`, then from `sources.book_id` for chapter-less scenes; delete any scene that
   resolves to neither (there should be none in production; log the count). Set `NOT NULL`, FK to
   `books.id ON DELETE CASCADE`, index `idx_scenes_book_id`. Guard every statement for the fresh-DB
   case, as ADR-002 §8 requires.
2. **Model.** `Scene.book_id` NOT NULL; `Book.scenes` relationship with
   `cascade="all, delete-orphan", passive_deletes=True`. Every scene-creating path sets it from the
   chapter.
3. **Chapter delete.** `DELETE /books/chapters/{id}` takes `?scenes=move&to={chapter_id}` or
   `?scenes=delete`; with scenes present and neither given, it returns 409 with the scene count so
   the UI can ask. Moving renumbers with the existing two-phase helper.
4. **Queries.** `list_book_scenes` filters `Scene.book_id == book_id` (drop the OR across source).
   Scene reads and writes authorise through the book (`Scene.book_id → Book.user_id`), keeping
   `user_id` as the tenant guard.
5. **Retire the user-level list.** `GET /api/v1/scenes/` is removed (the frontend no longer calls
   it after PR #28; confirm with a grep of `frontend/`). `GET/DELETE /api/v1/scenes/{id}` move under
   `/books/scenes/{id}` beside the existing content and revision routes, with the old paths kept as
   thin aliases for one release.

## Acceptance

1. After the migration on a database with scenes in chapters and source-only scenes, every scene
   has a `book_id` equal to its chapter's or source's book. *(Postgres integration test,
   `RUN_PG_TESTS`)*
2. Deleting a book deletes its scenes, beats and revisions. *(unit)*
3. Deleting a chapter that has scenes without a `scenes` parameter returns 409 and changes nothing;
   with `scenes=move&to=X` every scene lands in X at the end, positions contiguous. *(unit)*
4. No query in `app/` lists scenes by `user_id` alone. *(grep check)*
5. A fresh database migrated to head has the same `scenes` shape as an upgraded one.
   *(integration: compare `information_schema` for both paths)*

## Docs

BRD R3.1 extended to scenes ("scenes.book_id NOT NULL CASCADE"). ADR-002 §1 note. Feature set §10
"Book-scoped UI" row updated.
