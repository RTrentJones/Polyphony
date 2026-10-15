# SPEC-21 — Manuscript hygiene: clean export, honest outline warnings, upload types

**Closes**: B9 (backend half), B14, orchestrator finding #1 (noisy invented-names warning)
· **Size**: S · **Depends on**: — · **Wave**: 1 (package W1-F)

The export dialog UI (format, include synopsis, skip empty, range) is W1-E's; the promote-time
title strip and migration (B9's first acceptance line) wait for wave 2 (`app/api/plans.py`), and
can reuse `app.exports.builder.clean_chapter_title`.

## 21a — Export produces a clean manuscript (B9 backend)

`app/exports/builder.py`, `export_book` in `app/api/books.py`.

- **Headings.** `## Chapter {N}: {clean_title}`, where N is the chapter's 1-based position in
  the book and `clean_chapter_title` strips a leading outline number:
  `Chapter 1: `, `Chapter 12 - `, `chapter 3. `, `Chapter 4 – `, `Chapter Two: `, `CHAPTER IV:`.
  A digit may be followed by plain whitespace; a spelled-out or roman number needs punctuation or
  the end of the title, so `Chapter I Am Legend` is left alone. When nothing is left
  (`Chapter One`, `Chapter 7`) the heading is just `Chapter N`. The same heading is used in DOCX
  (Heading 1) and EPUB (`<h1>` and the TOC title).
- **Scene breaks.** Between consecutive scenes of a chapter, never before the first or after the
  last: `* * *` in Markdown, a centred `* * *` paragraph in DOCX (`WD_ALIGN_PARAGRAPH.CENTER`),
  `<hr/>` in EPUB.
- **`GET /books/{id}/export`** gains:
  - `skip_empty: bool = true`: chapters with no prose are left out;
  - `from_chapter`, `to_chapter: int ≥ 1` (optional): an inclusive range of book positions;
    `from_chapter > to_chapter` → 422; `0` → 422.
  Numbering always follows book position, both for a range (an export of 3–5 opens on
  "Chapter 3") and with skipped empties (chapters 1, 3, 5 keep those numbers). EPUB file names
  follow the same number (`chapter_3.xhtml`).
- `include_synopsis` is unchanged (default off).

**Tests**: `tests/unit/test_export_manuscript.py` (title stripping table; e2e-shaped book with
"Chapter N: …" titles and empty chapters: no heading with two `Chapter` tokens, breaks in all
three formats, skip/keep empties, range numbering, no input mutation);
`tests/unit/test_books_domain.py`; `tests/integration/test_books_api.py` (`TestExportEndpoint`:
the clean manuscript over HTTP, range, 422).

## 21b — Invented-names warning is quiet on ordinary prose (finding #1)

`app/planning/fidelity.py`. The baseline relay outline warned about `Desk`, `Following`,
`Debt In`, `Confetti That Dissolves At` and `Meeting That Could Have Been` (and, beyond the 12
shown, ~15 more: `Closing`, `Under`, `The Intake Atrium`, `Reply All The`, synopsis names such as
`Aeon Holdings` and `Marrow District`). Authors learn to ignore a warning like that. Causes and
fixes:

| Cause | Fix |
|---|---|
| The capitalised-run regex joined runs across `flatten_outline`'s two-space field separator (`Debt` ending a title + `In` opening the summary) | Runs are joined by a single space only; they never span a field, comma or line break. |
| Chapter/beat titles are title case | Titles still count toward principal recall, but the invented-names check reads only non-title fields: a phrase that appears only in a title is skipped. |
| Sentence openers (`At the all-hands`, `Under Zara's…`) | Leading/trailing function words are trimmed (`_STOP_CAPS` + `_CONNECTIVES`); a single sentence-opening word ending in `-ing/-ed/-ly` is dropped. |
| Title-case fragments | A phrase that still contains a function word (`That`, `Have`, `Been`, `At`…) is a fragment, not a name. |
| Common nouns capitalised (`Desk`, `Lanyard`) | A phrase whose every word is also used in lowercase in the outline or canon text is a common word. |
| Synopsis names (`Aeon Holdings`, `CEL`, `Marrow District`) | New `canon_text` keyword on `audit_outline` / `unknown_proper_nouns`: every capitalised phrase in the canon's authored text (`canon_known_text(canon)` = synopsis + rendered bible: cast, canon entries, style) is known; so is a phrase whose every word is a known token. Calendar words are never names. |

On the baseline outline, without `canon_text`: 27 → 9 flagged, all synopsis names; with it: 0.
A truly invented `Elara Moonwhisper` is still flagged. It remains a warning, never a gate (R1.5,
R1.6). Known limitation: a single-word invented name that only ever opens a sentence and ends in
`-ing/-ed/-ly` is not reported.

**Wiring (orchestrator).** `app/planning/staged_outline.py` (W1-C's file this wave) must pass
`canon_text=canon_known_text(canon)` at its two `audit_outline` calls for the synopsis-name part
to take effect in the app; until then the other fixes apply. The eval's own grader
(`evals/graders/fidelity.py`) is unchanged; W0-H reports `unknown_rate` over canon + synopsis.

**Tests**: `tests/unit/test_fidelity_gate.py` (verbatim baseline beats: none of the five words
flagged; synopsis names known with `canon_text`; `Elara Moonwhisper` flagged and still not a
gate; no cross-field runs; title-only skip; fragments and openers).

## 21c — Upload types agree between UI, server and docs (B14)

- `GET /api/v1/sources/config` (no auth) → `{accepted_extensions: settings.ALLOWED_EXTENSIONS,
  max_bytes: settings.MAX_UPLOAD_SIZE}`.
- `POST /sources/upload` checks the extension before reading the budget or the body: a `.doc`
  gets **415** "Old Word .doc files aren't supported. Save as .docx and upload again."; any other
  unsupported extension gets 415 "`.xyz` files are not supported. Accepted types: .txt, .docx,
  .pdf, .html, .htm" (previously 400 "Unsupported file format. Allowed: [...]").
- `AddSourceModal.tsx` reads the config (accept list, max size, helper text "Supported formats:
  TXT, DOCX, PDF, HTML, HTM (max 50 MB)"); its fallback constants `ACCEPTED_EXTENSIONS` /
  `MAX_UPLOAD_BYTES` equal the server's (a pytest reads the TS file). Picking or dropping a `.doc`
  shows the same "Save as .docx" message in place of the generic "File type not accepted", and the
  Upload button stays disabled. The client used to say `.doc` (and 10 MB) and refuse `.html`.
- **`.md` is not supported.** The parser has no Markdown path, so the orchestrator removes "md"
  from `docs/feature-set.md`'s upload row (below).

**Tests**: `tests/unit/test_upload_types.py`; `frontend/components/__tests__/AddSourceModal.test.tsx`.

## BRD / feature-set delta (for the orchestrator)

- `docs/feature-set.md`, Sources table: `| Upload a document (docx/pdf/txt/md) |` →
  `| Upload a document (docx/pdf/txt/html) — the dialog reads GET /sources/config; .doc gets "save as .docx" (SPEC-21) |`.
- `docs/feature-set.md`, Export row: notes → `Clean manuscript: "Chapter N: Title" by book
  position, scene breaks, empty chapters skipped by default, chapter range (SPEC-21)`.
- `docs/BRD.md` R1.5, append: "The warning reads only non-title outline text and treats every
  capitalised phrase in the canon's synopsis, cast, entries and style as known (SPEC-21)."
