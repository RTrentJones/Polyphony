# SPEC-27 — Suggested cast, the guided empty book, and the wave-2 journey

**Closes**: W2-2 (UI half: the "Add <name> to cast" chips), W2-10 / B12 (the getting-started
checklist and the dashboard's progress) · **Size**: M+ · **Depends on**: SPEC-24 (E2) computes
`suggested_characters`, SPEC-23 (E1) exposes it on the plans and promote responses and stores
`chapters.planned_beats`. Everything here degrades cleanly without them: no chips when the field is
absent or empty, and the planned-scene count falls back to the legacy `Planned beats:` summary.

## Problem

- The synopsis names Prava and Old Fen, but scenes could only cast the extracted cast. Making
  them castable meant opening Canon, adding a character by hand and retyping who they are.
- A new book opened on an empty Chapters tab, with eight tabs and eight unrelated empty states and
  no hint of the order (add source → review the cast → outline → scenes).
- The dashboard showed counts and dates only: nothing about how far each book had got, and no way
  back to the scene the author was last working on.

## Behaviour

### Suggested cast (`components/book/OutlinePanel.tsx`)

- An outline plan's `suggested_characters` (§1.2: `[{name, synopsis_sentence}]`, `[]` when there
  are none) renders as a "Suggested cast" box above the fidelity warnings, one button per person.
  Each button's accessible name is exactly **`Add <name> to cast`**; its title is the synopsis
  sentence. A missing or empty list renders nothing. Beat sheets never show it.
- A click calls the existing character-create API (`canonApi.createCharacter`, `POST /characters/`)
  with `{name, role: "minor", description: synopsis_sentence, book_id}` (an empty sentence sends no
  description). `minor` is not in `_PRINCIPAL_ROLES`, so this person never becomes a
  fidelity-gate principal.
- On success the panel refetches the plans (the server computes suggestions at read time, so the
  chip disappears once the person is cast) and the book's characters (`refreshCharacters`), then
  toasts **"<name> added — now castable"**. On failure (e.g. the name is already taken) it toasts
  the server's message and claims nothing. While one is being added, all chips are disabled.
- An outline beat (child) now also shows its `setting · tone` line when either is present
  (§1.1 fields). Nothing else about the tree changes.

### Getting started (`components/book/GettingStarted.tsx`, mounted in `app/books/detail/page.tsx`)

- Above the tab strip, a "Getting started" checklist with four steps and an `N/4 done` counter
  (`data-testid=getting-started-progress`). Each tick comes from real data, fetched from the same
  endpoints the tabs use:

  | Step | Done when | Action button | Goes to |
  |---|---|---|---|
  | 1. Add source | the book has ≥ 1 source (`GET /sources/?book_id=`) | Add your first source | opens the Add Source dialog |
  | 2. Review the cast | ≥ 1 character (`GET /characters/?book_id=`) | Review the cast | Canon tab |
  | 3. Generate an outline | an outline plan is `ready` with ≥ 1 node | Start planning | Outline tab |
  | 4. Draft a scene | ≥ 1 `completed` scene (`GET /books/{id}/scenes`) | Write a scene | Chapters tab |

- The first undone step is highlighted and its button is primary. Done steps are struck through
  and lose their button. Screen readers hear "(done)" / "(to do)" per step.
- It refetches whenever the active tab or the chapter count changes, so a step finished in one tab
  ticks when the author moves on (Add source navigates to the review page; coming back remounts it).
- It hides when all four are done, or when dismissed ("Dismiss getting started", stored per book in
  `localStorage` under `polyphony:getting-started:dismissed:<bookId>`, read and written in
  try/catch so blocked storage just means "dismissed for this page"). If any fetch fails it shows
  nothing rather than wrong ticks.
- The action buttons' names deliberately avoid the tab names and the page's other buttons
  ("Add source", "Outline", "Chapters", "Generate…"), so existing by-name lookups (the journey's
  `get_by_role("button", name="Outline")`) still match exactly one button. A vitest pins this.
- Synopsis does not tick step 1: the plan's acceptance is "0/4, then 1/4 after Add source", and a
  book created with a synopsis still has nothing to outline from but that one line.

### Book list progress (`app/api/books.py::list_books`, `app/dashboard/page.tsx`)

`GET /api/v1/books/` adds to each book (all integers ≥ 0):

| Field | Meaning |
|---|---|
| `word_count` | Σ `scenes.word_count` over the book's live scenes in a live chapter, or standalone |
| `chapter_count` | live chapters |
| `chapters_drafted` | live chapters with ≥ 1 live `completed` scene |
| `scenes_drafted` | live `completed` scenes (same scope as `word_count`) |
| `scenes_planned` | Σ over live chapters of `len(normalize_planned_beats(chapter.planned_beats))`, else the legacy `- title: summary` lines after `Planned beats:` in the summary |
| `last_edited_scene` | `{id, title, chapter_id, chapter_title, updated_at}` of the live scene with the newest `updated_at` (ties: highest id), else `null` |

It costs four queries for any number of books (books, chapters, two grouped scene aggregates, and
one `row_number()` window for the last-edited scene; sqlite ≥ 3.25 and Postgres both support it).
A scene in a trashed chapter, or trashed itself, counts nowhere. `planned_beats` is read with
`getattr`, so the endpoint works before and after SPEC-23's column lands.

Each dashboard book card now shows `"1,234 words · 3/8 planned scenes drafted · 2/5 chapters
drafted"` (`"N scenes drafted"` when nothing is planned; hidden on an API without the fields) and,
when there is one, a separate **"Resume: <scene title> (<chapter>)"** button to
`/books/editor?scene=<id>&book=<id>`. The card became a `div` holding two buttons (a button cannot
nest a button).

### Types (`frontend/lib/types.ts`)

`BookSummary` gains the optional progress fields and `last_edited_scene?: LastEditedScene | null`.
`PlanNode` gains optional `secondary_names`, `setting` and `tone` (§1.1). The day-0 additions are
unchanged.

## Not done (and why)

- B12's "Generate buttons that cannot succeed are disabled with a reason" lives in
  `ScenesPanel.tsx` / `ChaptersSection.tsx`, which this package does not own (E3 owns the
  `ChaptersSection` call site). Left for a later wave.
- B12's "first post-login screenshot is a full-page spinner" is the auth shell, not in scope.

## Tests

- Pytest `tests/unit/test_list_books_progress.py`: an empty book is all zeros with
  `last_edited_scene: null`; a mixed book counts words, drafted chapters and scenes, planned
  beats from the legacy summary, ignores a failed scene, a trashed scene and a scene in a trashed
  chapter, counts a standalone scene, and resumes the newest edit; `planned_beats` data wins over
  the summary and untitled beats are dropped; books are per book and per user.
- Vitest `components/__tests__/OutlineSuggestions.test.tsx`: chips render from the response (and a
  beat's `setting · tone`); none for `[]` or an absent field; a click posts
  `{name, role: "minor", description: <sentence>}` once, refetches plans and characters and toasts
  "Prava added — now castable"; a failed create toasts the error and no success.
- Vitest `components/__tests__/GettingStarted.test.tsx`: 0/4 with "Add your first source" next;
  1/4 after a source, and the other buttons deep-link; no button name collides with a tab or the
  page; loaded from mocked endpoints: 0/4 then 1/4 on refetch; a ready outline and a completed
  (not failed) scene count; hidden at 4/4; dismissal persists per book; hidden when a fetch fails.
- Browser (`scripts/e2e_browser.py`), new steps (existing steps unchanged, except `SYNOPSIS`, which
  now names "Prava, the intake caseworker"):
  - *add a suggested character to the cast* (after the staged outline): "Add Prava to cast" →
    toast → the chip is gone; the API cast has Prava with `role: minor` and the synopsis sentence;
    the scene cast picker lists her.
  - *planned scenes: beat cast plus POV, no default tone* (after generating all planned scenes):
    for each scene tied to a beat by `scene_request.planned_beat_id`, its requested
    `character_ids` equal the beat's ids plus its now-resolving `secondary_names` (else the
    chapter cast), filtered to the live cast, plus the chapter POV; no request has
    `emotional_tone: neutral`, and no prompt carrying a planned beat has `Emotional tone: neutral`.
  - *revise a selection: discard is byte-identical, accept is one revision* and *revise on a stale
    view is refused* (SPEC-25's panel; after the editor safety-net step, not directly after "edit a
    scene, load a revision", because the safety-net step depends on the draft that step leaves).
    The scene text is saved with an emoji and an em dash before the selected phrase. The diff
    shows `diff-delete` and `diff-insert`; Discard leaves the textarea and the saved scene
    byte-identical with no new revision; the newest proposal's `range` slices (Python code
    points) to exactly the selected phrase; Accept makes the editor equal the proposal, leaves it
    clean, saves it, and adds exactly one revision. "Redo beat N" on the hand-rewritten scene is
    refused with "The beat was edited; select the text instead."; a draft saved elsewhere
    between selecting and asking shows "The scene changed; reselect and try again."
  - *the review ranks by name mentions* (after the import-review step; SPEC-26's request): a
    scratch book "Review check" gets the same source pasted over the API; its review page shows
    "· N name mentions" and lists Milo Voss above Zara Okafor. A scratch book, because the
    journey's own review step commits and leaves the page.
  - *a new book's getting-started checklist*: 0/4 → "Add your first source" → paste → back on the
    book, 1/4.
  - *dashboard: progress and resume*: the Bored to Undeath card shows words and
    "N/M planned scenes drafted", and "Resume:" opens the editor.

## Docs (BRD / feature-set delta, for the orchestrator)

- `docs/BRD.md` §5.5, new requirement: "**R5.6** An empty book says what to do next. A
  getting-started checklist (Add source → Review the cast → Generate an outline → Draft a scene)
  ticks from the book's real data, deep-links each step, and hides when done or dismissed. The
  dashboard shows each book's words, scenes drafted out of planned, and resumes the last-edited
  scene. *(SPEC-27, B12)*"
- `docs/BRD.md` R4 or the SPEC-24 requirement for secondary people: append "The Outline panel offers
  each suggested person as an 'Add <name> to cast' chip that creates a `minor` character described
  by the synopsis sentence naming them (SPEC-27)."
- `docs/feature-set.md`, §1 Canon, new row: "Suggested cast from the outline | W2-2 | ✅ | Outline
  panel chips 'Add <name> to cast' for `suggested_characters` (people the outline names from the
  synopsis/canon but not the cast); creates a `minor` character with the synopsis sentence as its
  description; the chip goes once they are cast (SPEC-24, SPEC-27)".
- `docs/feature-set.md`, §10 Platform (or the UI section), new row: "Getting started checklist |
  R5.6 | ✅ | 0/4 → 4/4 from sources, characters, a ready outline and a completed scene; deep links;
  dismiss per book (SPEC-27)".
- `docs/feature-set.md`, new row: "Dashboard progress + Resume | R5.6 | ✅ | `GET /books/` returns
  `word_count`, `chapter_count`, `chapters_drafted`, `scenes_drafted`, `scenes_planned`,
  `last_edited_scene` (SPEC-27)".
- `docs/specs/README.md`: add SPEC-27 to the index.
