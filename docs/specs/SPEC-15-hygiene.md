# SPEC-15 — Hygiene and catalogue reconciliation

**Closes**: F18 (if SPEC-11 has not), F19, F20 · **Size**: S–M, deliverable in independent slices
· **Depends on**: —

Each slice below is a separate small PR and can be picked up at any time.

## 15a — Reconcile the catalogue (S, do first)

`docs/feature-set.md` still marks most of the book-as-root work as planned. The `polyphony-canon`
skill sends agents there first, so stale status misleads every future change.

- Re-derive every row's status from the code at the current head, citing the PR that shipped it.
- Add a "Status as of" date line at the top.
- Mark eval-audit backlog items closed where they are (item 4, `sanitize_for_llm`; item 9,
  `voice_chunks.book_id`) and link the open ones to their specs (items 1, 2 → SPEC-09; 3, 7 →
  SPEC-02; 5 → SPEC-01; 6 → SPEC-03; 8 → SPEC-06's judge note).
- Add a short "Specs" link to the README beside the BRD/ADR pointers.

**Acceptance**: every ✅ row cites a file or PR; every ⬜ row cites a spec.

## 15b — Small correctness defects (S)

- `add_thread_event` (`app/api/plans.py:488`): require that `scene_id` belongs to a scene in the
  thread's book and `chapter_id` to a chapter in the thread's book; 404 otherwise.
- Export (`app/api/books.py:820`): add `include_synopsis: bool = False`; the storyboard is not part
  of the manuscript by default.
- Promote position conflict: wrap in the same retry-once pattern as `create_chapter` (SPEC-10 may
  absorb this).
- `delete_character`: delete chunks and row in one transaction (SPEC-13 may absorb this).
- `test-dialogue`: call `check_user_budget` (SPEC-07 may absorb this).

**Acceptance**: one unit test per item.

## 15c — Retire transition scaffolding (S)

- Delete `app/migrate.py`'s legacy-reset shim once production and beta are confirmed on the
  `sources` schema, and change the Dockerfile CMD to `alembic upgrade head`. Keep the
  `POLYPHONY_FORCE_SCHEMA_RESET=1` escape hatch only if an operator still wants it, documented in
  the README.
- Read the app version from one place (`settings.APP_VERSION`, set from the image's
  `GREENLIGHT_SHA` short form or a package version) instead of the `"1.0.0"` literals in
  `app/main.py`.
- Remove the deprecated `characters: list[str]` input once SPEC-01's one-release grace period ends.

## 15d — Frontend structure and tests (M)

- Split `frontend/app/books/detail/page.tsx` (2,258 lines) into one file per panel under
  `frontend/components/book/` (Chapters, Scenes, Sources, Outline, Threads, Continuity, Canon) plus
  the GenerateScene modal, with no behaviour change.
- Add Vitest + React Testing Library with tests for the status badges (all job states from SPEC-07),
  the KeyValueEditor, and the cast picker, and run `npm test` in `test.yml`.
- Introduce the minimal `BookProvider` the feature set already planned, to stop prop-drilling `?id=`.

**Acceptance**: the split PR is a pure move (the diff is reviewable as moves); CI runs frontend
tests.

## 15e — Drop `character_chunks` (S, if SPEC-11 has not)

Stop writing it, migrate it away, and remove the model. See SPEC-11 step 5.
