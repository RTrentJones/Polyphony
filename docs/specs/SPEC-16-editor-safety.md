# SPEC-16 — Editor safety net

**Closes**: B1 · the editor part of B13 · **Size**: S · **Depends on**: —

## Why

In the scene editor, a misclick on "Back to book" threw away unsaved revision work without a word.
The page tracked `isDirty` but had no `beforeunload`, no route guard and no autosave. The revision
preview was a custom overlay: it had no `role="dialog"`, no Escape and no focus management. That
breaks invariant 3 (nothing the author wrote is destroyed) at the point where the author writes most.

## Outcome

- With unsaved changes, every way out of the editor asks first: "Back to book" (both buttons), any
  in-app link (the Navbar included), browser Back, and tab close or reload. Cancelling leaves the
  author on the editor with the text intact.
- While the text differs from the saved text, it is autosaved to a **local** draft
  (`localStorage`, key `polyphony:draft:<sceneId>`). It is written at most every 5 s and no later
  than 5 s after the first unsaved keystroke, and it is flushed on `pagehide` and when the editor
  unmounts. Autosave never calls the API, so it never creates a `scene_revisions` row.
- When a scene is reopened and has a draft that differs from the server text, the editor shows
  **"Restore unsaved draft from HH:MM?"** with **Restore draft** / **Discard draft**. The banner
  also notes when the saved scene has changed since the draft was written. Restore puts the draft
  text into the editor. The text stays unsaved, so the guard and autosave stay active, and there is
  no server call. A draft identical to the server text is dropped silently.
- A successful Save clears the draft and turns the guard off.
- The revision preview is a modal dialog: `role="dialog"`, `aria-modal="true"`, labelled by its
  heading. Focus moves into it when it opens, Tab and Shift+Tab cycle inside it, Escape closes it,
  and focus returns to the revision button that opened it. Loading a revision over unsaved text now
  asks first. "Save to keep it" is unchanged.

## Design

| Piece | File |
|---|---|
| Draft storage + `useDraftAutosave` | `frontend/lib/draft-store.ts` |
| `useUnsavedChangesGuard(isDirty)` → `{ guardedPush, confirmLeave }` | `frontend/lib/useUnsavedChangesGuard.ts` |
| Wiring, banner, dialog focus (`useModalDialog`) | `frontend/app/books/editor/page.tsx` |

**Guard.** `next/navigation` exposes no route-change event that can be vetoed, so each exit is
covered on its own:

1. `beforeunload` while dirty covers tab close, reload, a typed URL and cross-origin links.
2. A **capture-phase** `click` listener on `document` handles same-origin `<a>` clicks (the
   Navbar's `<Link>`s included). It runs before React's root listener, so on cancel
   `preventDefault` + `stopPropagation` keep `<Link>` from navigating. `Navbar.tsx` is unchanged.
   Modified clicks (new tab), `target=_blank`, `download` and same-page (`#hash`) links are left
   alone.
3. **Back**: the first time the page turns dirty, one sentinel history entry is pushed (same URL,
   with Next's own `history.state` copied, because the app router reloads on a `popstate` whose
   state it does not recognise). Back pops the sentinel, and the `popstate` handler asks. Cancel
   re-pushes the sentinel. Accept, or a page saved in the meantime, steps back once more, so one
   press of Back still leaves.
4. Programmatic navigation goes through `guardedPush(href)`. Once the sentinel exists, leaving uses
   `router.replace`, so the history never gains a phantom editor entry. **Any future in-editor
   navigation (e.g. W2-E's Prev/Next) must call `guardedPush`**; a bare `router.push` bypasses the
   guard.

Accepting the confirm keeps the local draft. That is what lets the author change their mind and
recover it on reopen.

**Draft store.** Every `localStorage` access is in try/catch. With blocked, full or missing
storage there is simply no draft, and the guard still works. A malformed stored value reads as no
draft. The record is `{text, savedAt, baseHash}`. `baseHash` is an FNV-1a
fingerprint of the server text the draft was edited from, used for the "changed since" note.
The editor is keyed by scene, so a `?scene=` switch remounts it: the old scene's unsaved text is
flushed under its own key on unmount, and nothing carries over to the new scene.

**Autosave hold.** While the Restore/Discard banner is showing, autosave is held so the older
draft is not overwritten before the author chooses. The guard still covers typing in that window.

**Unchanged:** the page's data fetching and the `PUT /books/scenes/{id}/content` call shape, which
W2-A builds on.

## Tests

- `frontend/lib/__tests__/draft-store.test.ts`: round-trip under the key, malformed values,
  thrown storage errors swallowed, autosave fires ≤5 s during continuous typing (fake timers),
  clears when the text returns to the saved text, held while disabled, flushed on `pagehide` and
  on unmount.
- `frontend/components/__tests__/SceneEditorGuard.test.tsx` (the whole page with a mocked router
  and API): no prompt when clean; "Back to book" asks and cancel keeps the text; accept leaves via
  `replace`; Navbar-style anchors are intercepted; `beforeunload` only while dirty; the Back
  sentinel re-arms on cancel and steps back on accept; Save clears the draft and the guard; the
  banner shows "from HH:MM" and Restore yields the draft text without a save call; Discard; an
  identical draft is dropped; loading a revision over unsaved text asks; the revision dialog is
  labelled and modal, traps Tab, closes on Escape and returns focus.

## Browser steps (for `scripts/e2e_browser.py`, after "edit a scene, load a revision")

The journey's global dialog handler auto-accepts, so these steps need a handler that can dismiss.
Use exact names for the banner buttons: `name="Restore"` without `exact` also matches "Restore
draft".

1. Type into the editor, click "Back to book", dismiss the dialog. Assert the page is still the
   editor and holds the typed text. Repeat with browser Back (`page.go_back()`).
2. Click "Back to book" again and accept. Assert the book page.
3. Reopen the scene and reload. Assert `Restore unsaved draft from \d\d:\d\d` is visible. Click
   **Restore draft** (exact) and assert the editor value equals the typed text.
4. Save, reload. Assert no banner, and that `GET /books/scenes/{id}/revisions` grew by exactly 1.
5. Open "Revision 1", press Escape. Assert the dialog is hidden and focus is back on the revision
   button.

## BRD / feature-set delta (applied by the orchestrator)

- **BRD §5.5**, add: *R5.5 The scene editor never loses unsaved work. Leaving with unsaved changes
  (in-app link, Back, tab close) asks first. The text autosaves locally every ≤5 s without creating
  server revisions and is offered back on reopen ("Restore unsaved draft from HH:MM"). Saving
  clears the draft.*
- **feature-set §5 (Versioning)**, add a row: `Scene editor safety net (unsaved-changes guard,
  local draft autosave + restore) | R5.5 | ✅ | frontend/lib/useUnsavedChangesGuard.ts,
  frontend/lib/draft-store.ts; local only, never a scene_revisions row (SPEC-16)`.
- **feature-set**, accessibility note (B13 partial): revision preview is a modal dialog with
  focus trap, Escape and focus return (SPEC-16). The remaining B13 items (form labels, review
  labels, axe gate) are not covered here.
