# SPEC-25 — Revise UI, and planned beats read as data in the frontend

**Closes**: backlog W2-3 (B3 UI; the backend is SPEC-19) and the frontend half of W2-1's
planned-beats migration (SPEC-23) · **Size**: M+ · **Depends on**: SPEC-19, SPEC-23 (day-0
`PlannedBeat` type), SPEC-16

## Why

The revise API (SPEC-19) exists, but an author cannot reach it: "make Zara colder here" still means
hand-editing or regenerating the whole scene. Separately, SPEC-23 moves a promoted chapter's planned
beats from "Planned beats:" lines in its summary to `chapters.planned_beats`. The frontend found
beats by parsing the summary, so after the migration the "Generate all planned scenes" button, its
menu entry and the modal's beat chips would all disappear.

## Outcome

- From the scene editor, **Revise selection…**, **Redo beat N** and **Whole scene…** ask for an AI
  revision. The result appears as a side-by-side word diff with **Accept** and **Discard**.
  - Discard leaves the editor text byte-identical.
  - Accept makes the proposal the draft. The previous draft becomes one more entry in Revisions,
    and the editor is not dirty afterwards, because the server saved the text.
- Chapters keep their planned-beat button and chips whether the beats arrive as data or, for
  chapters promoted before the migration, as legacy summary lines.

## Design

### Revise (`components/editor/RevisePanel.tsx`, `app/books/editor/page.tsx`)

- **Target.**
  - *Selection.* The textarea's `selectionStart/End` at the moment the author clicks.
  - *Beat.* The buttons are built from `GET /books/scenes/{id}/grounding` → `beats[].beat_index`
    and labelled 1-based. The server locates the beat's own prose.
  - *Whole scene.*
- **Offsets** (`lib/text-offsets.ts`).
  - The server's draft is `scene_text(scene)`: the stripped text.
  - The selection is shifted past the editor text's leading whitespace, clamped to the stripped
    draft, and converted from UTF-16 code units to **Python code points**. An emoji counts 2 in JS
    and 1 in Python.
  - `selected_text` is the draft sliced at those code points, so it is exactly what the server's
    `draft[start:end]` returns.
- **Guards.**
  - Every request sends `base_text_hash` (the SHA-256 hex of the UTF-8 stripped draft, via
    `crypto.subtle`; equal to `revise.text_hash`).
  - Selection and whole-scene requests also send `selected_text`. A beat request does not: the
    client has no beat prose (`/grounding` gives only the description), so the hash guards it.
  - A 409 shows the server's message plus "The scene changed; reselect and try again." The 409s
    that already say what to do (a beat edited since generation, a scene still generating, a
    decided proposal) are shown without the suffix.
- **Only the saved draft is revised.** With unsaved edits, the buttons are disabled and the panel
  says "Save your edits first". While the scene is `processing` or `paused` they are also disabled,
  because the server refuses it.
  - If the author edits while a proposal is on screen, Accept asks before replacing those unsaved
    edits.
- **Job.** `POST .../revise` returns a job, which `useJob` polls with the shared backoff (a paused
  job says "Waiting for AI quota…").
  - On success the panel reads `GET .../proposals`, takes the proposal whose `job_id` matches, and
    diffs it against that response's `draft`.
  - On failure or cancel it shows the job's error, and nothing changes.
  - A `deduplicated` response follows the job that is already running.
- **Reload.** On open, a `proposed`, non-`stale` proposal is offered again, so a reload during the
  job loses nothing.
- **Proposals are not revisions.** `GET .../revisions` no longer lists `ai_revise` rows. The
  Revisions list shows only snapshots, so an accept adds exactly one entry (the previous draft).
- **Accept.** `POST .../accept` once. The editor's `content` and `savedContent` both become the
  proposal, `lastSavedAt` becomes the server's `updated_at`, the local draft (SPEC-16) is cleared,
  and Revisions reloads.
- **Discard.** `POST .../discard`. The textarea value is never touched.
- **Navigation** stays on `useUnsavedChangesGuard`'s `guardedPush`. The panel adds no links.

### Word diff (`components/editor/DiffView.tsx`)

- The text is tokenised into words and whitespace runs, so the parts rejoin to both texts exactly.
- The common prefix and suffix are peeled off first, and an LCS table covers only the changed
  middle. Above 4M cells, the middle is shown as one deletion plus one insertion.
- A whitespace-only equal run between two changes is folded into them, so "~~smiled warmly~~
  stared coldly" reads as one change.
- The left column ("Current draft") marks deletions with `<del>`. The right column ("Proposed
  revision") marks insertions with `<ins>`.
- No new npm dependency.

### API module (`lib/revise-api.ts`)

`reviseApi.revise / proposals / accept / discard` and the types `RevisionProposal`, `ReviseRequest`,
`ReviseResponse` and `ProposalsResponse`. These live here rather than in `lib/types.ts`, per wave-2
file ownership.

### Planned beats as data (`GenerateSceneModal.tsx`)

- `plannedBeats(chapter): PlannedBeat[]` returns `chapter.planned_beats` when it is non-empty.
  Otherwise it parses the legacy "Planned beats:" lines into the same shape:
  - split on the first `": "`;
  - a line without `": "` is a title with an empty summary;
  - `node_id` is `legacy-<i>`, and the other fields are empty.
- `beatDescription(beat)` is `` `${title}: ${summary}`.trim() ``, byte-identical to the server's
  `beat_scene_description`.
- Call sites: the modal's chips, `GeneratePlannedScenesButton`, and the chapter row's ⋯ menu
  (`ChaptersSection`). All three now pass the chapter, not its summary.
- The modal shows each beat's setting and tone (read-only) under its chip. Picking a beat fills the
  description, and fills the setting too when that field is still empty. Editing beats is W2-7.

## Tests

- `lib/__tests__/text-offsets.test.ts`:
  - code points with an emoji and an em dash before the selection;
  - round trip;
  - selection over padded editor text;
  - the SHA-256 "abc" vector, a UTF-8 vector checked against Python `hashlib`, and the empty
    string.
- `components/__tests__/DiffView.test.tsx`: inserted and deleted words marked; both texts rebuilt
  exactly; deletions only on the left, insertions only on the right.
- `components/__tests__/RevisePanel.test.tsx`, through the real editor page:
  - the request carries code-point offsets, `selected_text` and the draft hash;
  - the diff appears after the job succeeds;
  - Discard leaves the textarea byte-identical;
  - Accept calls `/accept` once, leaves the editor not dirty, and Revisions gains an entry;
  - a 409 renders the message;
  - Redo beat N sends `beat_index`;
  - an empty selection and unsaved edits are refused;
  - a pending proposal is offered again after a reload.
- `GeneratePlannedScenesButton.test.tsx`: `plannedBeats` reads the field, falls back to the
  summary, and returns `[]` when there is neither. The button renders for a chapter with
  `planned_beats` and an empty summary.
- `GenerateSceneModal.test.tsx`: chips come from `planned_beats`, show the setting and tone, and
  fill the setting.
- `ChaptersSection.phone.test.tsx`: the promoted fixture is now post-migration (beats as data,
  plain summary), and the menu entry still appears.

## Docs

- **BRD delta:** the scene editor can request an AI revision of a selection, a beat or the whole
  scene and review it as a word diff before accepting. Nothing changes until Accept, and the
  previous draft is kept as a revision (R4.x).
- **feature-set delta:** under the scene editor, add "Revise with AI (selection / beat / whole
  scene) → side-by-side word diff → Accept / Discard". Under chapters, note that planned beats are
  read from `planned_beats`, with the legacy summary as fallback.
