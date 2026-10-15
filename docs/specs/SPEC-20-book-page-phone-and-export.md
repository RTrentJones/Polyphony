# SPEC-20 — Book page at phone width, and the export dialog

**Closes**: B4 (phone layout), B9 UI (export dialog) · **Size**: S · **Depends on**: SPEC-21
(W1-F) for the export *content* (clean headings, scene breaks, empty-chapter skipping, ranges);
the dialog works before it, because FastAPI ignores unknown query parameters.

## Problem

- At 390 px a chapter row showed only the chevron, the arrows and wrapped buttons. The title was
  `truncate`d to nothing, "Generate all planned scenes" was clipped, and the e2e phone step still
  passed, because the card's `overflow-hidden` hid the overflow from `scrollWidth`.
- Only 3 of the 8 book tabs fit, with no sign that the strip scrolls; a deep link to a later tab
  (`?tab=trash`) opened with the active tab off screen.
- After a reorder the list read "3. Chapter 2: Milo Voss": promoted outline titles carry their own
  stale number.
- Export was three bare buttons (MD / DOCX / EPUB). `include_synopsis` existed in the API but had
  no UI, and there was no way to skip empty chapters or export a range.

## Behaviour

### Chapter rows (`components/book/ChaptersSection.tsx`)

- Below `sm:` a row is two lines: the title block (`min-w-0`, `line-clamp-2`, `break-words`, never
  `truncate`), then the actions. From `sm:` it is one line, as before.
- The up/down arrows and "Generate scene" stay in the row at every width. Below `sm:`,
  "Generate all planned scenes", "History" and "Delete" move into a "⋯" `RowActionsMenu`; from
  `sm:` they stay in the row and the menu is hidden. The menu's "Generate all planned scenes"
  makes the same call, and shows the same messages, as `GeneratePlannedScenesButton`. It is listed
  only when the chapter has planned beats.
- The row card no longer clips (`overflow-hidden` removed; the header rounds its own corners), so
  the menu can drop below the row and nothing is hidden from the e2e check.
- Scene rows inside an expanded chapter wrap below `sm:`; the preview text takes its own line.
- `displayChapterTitle(title)` (`lib/utils.ts`) strips a leading `Chapter \d+` followed by `:` `.`
  `-` `–` or `—` (case-insensitive) **at render only**. A title that is nothing but the prefix is
  shown unchanged. Buttons' accessible names, the row's `data-chapter-title`, History and Trash
  keep the stored title (W2-B cleans stored titles at promote time and migrates old ones).

### `RowActionsMenu` (`components/book/RowActionsMenu.tsx`)

The WAI-ARIA menu-button pattern. The trigger has `aria-haspopup="menu"` and `aria-expanded`;
the popup is `role="menu"` with `role="menuitem"` items. Click, Enter, Space or ArrowDown/Up on
the trigger opens it and focuses the first enabled item. ArrowUp/ArrowDown wrap and skip disabled
items, and Home/End jump to the ends. Escape closes it and returns focus to the trigger; Tab or a
click outside closes it. Choosing an item closes the menu, then runs the item.

### Tab strip (`app/books/detail/page.tsx`, `BookTabs`)

- The strip still scrolls on its own, not the page. When more tabs lie to one side, that edge
  shows a fade into the page background and a chevron button ("Scroll tabs left/right") that
  scrolls the strip by about 70% of its width.
- The active tab is `scrollIntoView({inline: "nearest", block: "nearest"})`'d on mount (deep links)
  and whenever it changes. The overflow state is re-measured on scroll and resize.
- The `nav` is labelled "Book sections".

### Export dialog (`components/book/ExportDialog.tsx`)

The header's MD / DOCX / EPUB buttons are replaced by one **Export** button that opens a dialog
with these controls:

| Control | Default | Query param |
|---|---|---|
| Format: Markdown / Word / EPUB (radio) | Markdown | `format=md\|docx\|epub` |
| Include synopsis | off | `include_synopsis=true\|false` (always sent) |
| Skip empty chapters | on | `skip_empty=true\|false` (always sent) |
| From chapter / To chapter (1-based, inclusive, by book position; blank = all) | blank | `from_chapter`, `to_chapter` (omitted when blank) |

**API contract with SPEC-21 (W1-F):**
`GET /api/v1/books/{id}/export?format=&include_synopsis=&skip_empty=&from_chapter=&to_chapter=`.
The server answers 422 when from > to, and numbers headings by book position (a range that
starts at 3 is headed "Chapter 3"). `exportQuery()` in `lib/books-store.ts` builds the params,
and `booksApi.exportBook(bookId, format, fallbackTitle, options?)` sends them. Without `options`,
only `format` is sent, as before.

The dialog validates the range before sending: whole numbers from 1 to the chapter count, and
from ≤ to. It shows the error inline, with `role="alert"` and `aria-invalid` on the fields, and
disables Download until the range is valid. A server error keeps the dialog open and shows the
message. A successful download closes it.

## Tests

- Vitest `components/__tests__/ExportDialog.test.tsx`: `exportQuery` builds the query strings
  (defaults, full range, open-ended); `exportBook` passes the params, and sends only the format
  without options; `validateChapterRange` cases; the dialog's defaults, the options it sends, an
  impossible range blocked, and a server error shown with the dialog kept open.
- Vitest `components/__tests__/ChaptersSection.phone.test.tsx`: `displayChapterTitle` cases;
  the row shows "3. Milo Voss, …" with `line-clamp-2` and no `truncate`; the stacked layout and
  the `hidden sm:flex` / `sm:hidden` split; the menu offers planned / history / delete, and runs
  planned scenes with the same toast; a chapter without beats has no planned item; RowActionsMenu
  keyboard behaviour (open, arrows skip disabled, Home/End, Escape returns focus, outside click).
- Browser (`scripts/e2e_browser.py`):
  - *export the book*: drives the dialog and checks its defaults. The default MD excludes the
    synopsis; a second export with "Include synopsis" includes it. No Markdown heading contains
    two `Chapter` tokens (always checked).
  - *the book at phone width*: the right-hand scroll affordance shows. With a chapter expanded,
    every chapter row's title is visible with ≥ 20 characters (or all of a shorter title) actually
    rendered inside its box, counted per glyph. Every visible button's box lies inside its row
    and inside the viewport. The "⋯" menu opens with focus on its first item, fits on screen and
    closes on Escape. After each tab click the active tab is fully in view. `?tab=trash` opens
    with Trash in view and the left affordance shown. The no-sideways-scroll check is kept.
  - The reorder and planned-scenes steps find rows by `data-chapter-title` (the stored title),
    since the visible title is now the cleaned one.

## BRD / feature-set delta (for the orchestrator)

- `docs/feature-set.md` §3, "Export" row, Notes: "Export dialog: format, include synopsis
  (default off), skip empty chapters (default on), chapter range (1-based, by book position)
  (SPEC-20); clean `Chapter N: title` headings and scene breaks (SPEC-21)".
- `docs/feature-set.md`, new row (UI section or §3): "Book page usable at phone width | — | ✅ |
  Chapter rows stack title over actions below `sm:`, secondary actions in a "⋯" menu; tab strip
  shows a scroll affordance and keeps the active tab in view (SPEC-20)".
- `docs/specs/README.md`: add SPEC-20 to the index.
- BRD: no requirement change.
