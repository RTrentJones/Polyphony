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
- Chapters keep their planned-beat button and chips, read from `chapter.planned_beats` (the
  server's only source after migration 0019; wave 2.5 removed the legacy summary parser).
- A single scene generated from a planned beat uses the beat's tone and cast and is linked to the
  beat by `planned_beat_id`, so "Generate all planned scenes" skips it (wave 2.5, review S7).

## Design

### Revise (`components/editor/RevisePanel.tsx`, `app/books/editor/page.tsx`)

- **Target.**
  - *Selection.* The textarea's `selectionStart/End` at the moment the author clicks.
  - *Beat.* The buttons are built from `GET /books/scenes/{id}/grounding` → `beats[].beat_index`
    and labelled 1-based. The server locates the beat's own prose. `/grounding` lists every stored
    `SceneBeat` (beats without a recorded grounding come back with no characters), so any scene
    with stored beats offers "Redo beat"; `GET /scenes/{id}` also returns `beats`.
  - *Whole scene.*
- **Offsets** (`lib/text-offsets.ts`).
  - The server's draft is the raw editable text, edge whitespace included (since `be0bc9d`), so
    the editor text **is** the draft; there is no client-side transform (wave 2.5 deleted the
    identity `serverDraft`).
  - The selection is clamped to the draft and converted from UTF-16 code units to **Python code
    points**. An emoji counts 2 in JS and 1 in Python.
  - `selected_text` is the draft sliced at those code points, so it is exactly what the server's
    `draft[start:end]` returns.
- **Guards.**
  - Every request sends `base_text_hash` (the SHA-256 hex of the UTF-8 draft, via
    `crypto.subtle`; equal to `revise.text_hash`).
  - Selection and whole-scene requests also send `selected_text`. A beat request does not: the
    client has no beat prose (`/grounding` gives only the description), so the hash guards it.
  - A 409 shows the server's message plus "The scene changed; reselect and try again." The 409s
    that already say what to do are shown without the suffix (`SELF_EXPLAINED_409` in
    `lib/revise-api.ts`): "select the text instead" (a beat edited since generation), "still being
    generated", and "already accepted/discarded".
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

- The frontend reads `chapter.planned_beats` and nothing else. A chapter whose `planned_beats` is
  empty shows no button, no menu entry and no chips, whatever its summary says (SPEC-23: the
  server normalises the column, and migration 0019 moved every legacy block into it).
- `beatDescription(beat)` is `` `${title}: ${summary}`.trim() ``, byte-identical to the server's
  `beat_scene_description`.
- Call sites: the modal's chips, `GeneratePlannedScenesButton`, and the chapter row's ⋯ menu
  (`ChaptersSection`).
- The modal shows each beat's setting and tone (read-only) under its chip. **Picking a beat**:
  - fills the description, and the setting when the beat has one;
  - sets the tone to the beat's tone (a free-text tone not in the list is added as an option);
  - preselects the beat's `character_ids` that are still in the book, when there are any (an empty
    list means the chapter's cast, which stays selected); a POV no longer in the selection is
    cleared;
  - sends `planned_beat_id` with the request, so the scene is linked to the beat by id (SPEC-23
    §beat identity). Changing the target chapter clears the link.
- **Tone.** The default is `''`, shown as "Book's tone": the canon's style decides. `'neutral'` is
  no longer offered; the backend treats only `""` as no tone.
- Editing beats is W2-7.

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
- `GeneratePlannedScenesButton.test.tsx`: the button renders for a chapter with `planned_beats`
  and an empty summary, and renders nothing without `planned_beats` (even when the summary still
  holds a "Planned beats:" block).
- `GenerateSceneModal.test.tsx`: chips come from `planned_beats` and show the setting and tone;
  none show without them; the default tone is `''` with no `'neutral'`; picking a beat sets the
  description, setting, tone and cast and sends `planned_beat_id`; a beat with no cast keeps the
  chapter's cast; no beat picked sends no id.
- Browser journey (`scripts/e2e_browser.py`): after "generate all planned scenes", the modal
  picks beat 1, shows its tone and cast, and the generated scene's `scene_request.planned_beat_id`
  is the beat's `node_id`; "Redo beat 2" on a planned scene opens a proposal.
- `ChaptersSection.phone.test.tsx`: the promoted fixture is now post-migration (beats as data,
  plain summary), and the menu entry still appears.

## Docs

- **BRD delta:** the scene editor can request an AI revision of a selection, a beat or the whole
  scene and review it as a word diff before accepting. Nothing changes until Accept, and the
  previous draft is kept as a revision (R4.x).
- **feature-set delta:** under the scene editor, add "Revise with AI (selection / beat / whole
  scene) → side-by-side word diff → Accept / Discard". Under chapters, note that planned beats are
  read from `planned_beats` only, and that a single scene from a planned beat takes its tone and
  cast and is linked to it (wave 2.5).
