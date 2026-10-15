# SPEC-31 — Located continuity: each finding quotes the prose and opens it

**Closes**: W3-4 (N7; B10 / W2-8 core) · **Size**: M · **Depends on**: SPEC-02 (continuity v2),
`Chapter.number` (wave-3 day-0). **Amends**: SPEC-02 design items 4 and 6.

## Problem

The checker found the right problems, but an author could not see where they were. The whole
book was joined and cut every 2,500 words, so the model could not tell which scene it was reading;
it was asked for a "quote or location hint" (`refs`), and the panel printed "Ref: <hint>" as plain
text. No finding quoted the prose (`quote_verbatim` 0.0). The prompt also told the model to compare
against "the <prior> tail and the earlier findings" on the first chunk, where neither exists.

## Behaviour

### Input: one path (`app/planning/continuity.py`)

- `collect_scenes(session, book_id, chapter_id) -> list[ProseScene]` is the one continuity input.
  It returns the live scenes of the chapter (or the book) in chapter, then scene, order, skipping
  scenes with no prose. Each `ProseScene` is `(scene_id, chapter, chapter_title, scene,
  scene_title, text)`: `chapter` is `Chapter.number`, `scene` is the 1-based ordinal among the
  chapter's live scenes, and `text` is `exports.builder.scene_text` (the trimmed draft). The job
  handler (`_run_continuity`), `start_continuity_check` and the continuity estimate all call it and
  count `sum(len(s.text.split()))`. `plans._collect_prose` and the `app/jobs` → `app/api` import
  are deleted.

### Chunks by scene

- `chunk_scenes(scenes, chunk_words=CHUNK_WORDS) -> list[Chunk]` packs **whole scenes** into a
  chunk while their prose stays within `CHUNK_WORDS` (2,500). A longer scene splits at paragraph
  breaks (blank lines, kept), and by words only inside a single over-long paragraph. Each scene,
  and each piece of a split scene, opens with its marker line:

  ```
  [Ch {chapter} "{chapter_title}" › Scene {scene} "{scene_title}"]
  ```

  (`SCENE_MARKER`). A `Chunk` is `(text, scenes)`, where each
  scene carries the text of its piece in that chunk. `chunk_prose` is deleted.

### Resume

- The checkpoint's `input_hash` is taken over the fact sheet and the **chunk texts, markers
  included**. A check paused before this change saved a hash of the joined prose and a word-chunk
  index; it never matches, so the check restarts at chunk 0 rather than skip prose or merge
  `refs`-shaped findings (review S2).

### Findings

- The model is asked for `{"type", "severity", "detail", "quote"}`, the quote "copied exactly from
  the prose, at most 200 characters", and told never to quote a marker line. The model does not
  name the scene.
- `validate_findings(raw, chunk) -> list[Finding]` stores `quote` as given (trimmed, at most 200
  characters, a prefix so it stays verbatim) and sets `scene_id` itself: the first scene in the
  chunk whose text contains the quote, both whitespace-normalised with outer quotation marks
  (`" ' “ ” ‘ ’`) stripped. A quote that is not found, or no quote, gives `scene_id: null`. **No
  finding is dropped** for its quote. There is no `located` field: it would restate
  `scene_id is not None` (review S1).
- `dedupe_findings` merges on `(type, normalised detail)` or an equal non-empty normalised quote,
  keeping the most severe. The `refs` branch is deleted.
- Stored shape (`ContinuityReport.findings`, JSON, no migration):
  `{type, severity, detail, quote, scene_id}`. Reports written before this change keep `refs` and
  have no `quote`; no code reads `refs`.

### Absent blocks by construction

- `<earlier_findings>` and `<prior>` are each built together with the one sentence that mentions
  them ("Findings already reported (do not repeat them):", "The end of the previous section, for
  contradictions across the boundary:"). With no earlier findings or no carry, the block and its
  sentence are both absent, so the first chunk's prompt neither carries nor mentions them. No
  prompt_contract check is needed (wave-3 plan, Could 2).

### Panel (frontend)

- `FindingRow` (`ContinuityPanel.tsx`) shows the quote under the detail. With a `scene_id` it adds
  an **"Open in editor"** link to `/books/editor?book=<bookId>&scene=<scene_id>`; with a quote and
  no `scene_id` it says **"could not locate"**. A pre-SPEC-31 finding renders its detail only. The
  `Ref:` line is deleted.

### Stub LLM

- The continuity branch of `evals/stub_llm.py` answers one `minor` finding quoting the first
  sentence of the line after the first marker in `<prose>`, so the journey has a located finding to open.

## Not done (and why)

- Selecting the quote in the editor (backlog cut line 2, code-gate NB-1): the panel shows the
  quote and the editor opens its scene, so the author finds it with the browser's search. A client
  copy of the server's quote-matching rule would have to be kept in step with `_normal`.
- "Mark fixed" / "Intentional (ignore)" with suppression across runs: W3-11, deferred.
- The eval harness's own reading of findings (`evals/graders/continuity.py`'s `refs` text,
  `pipeline._finding_quote`'s `refs` fallback) belongs to the harness package (E4a).

## Tests

- `tests/unit/test_continuity_v2.py`:
  - (F) three 1,000-word scenes chunk as `[s1, s2]`, `[s3]`, each under its marker; a 6,000-word
    scene gives three marked pieces, split at paragraphs (or by words in one long paragraph);
  - (F) a quote in scene 2 with different line breaks and outer quotation marks gets
    `scene_id = s2`; a paraphrase and a quote-less finding are kept with `scene_id None`; no
    `refs`, no `located`;
  - (F) the first chunk's prompt has no `<prior>`, no earlier findings and no sentence about
    either; the second has both;
  - (F, S2) a checkpoint saved under the pre-SPEC-31 hash of the same prose restarts at chunk 0
    and drops its findings;
  - the fence, incomplete, failed, dedupe (now also by quote) and pause/resume tests, on scenes.
- `tests/unit/test_trash.py`: `collect_scenes` skips trashed chapters and scenes and numbers the
  rest in order. `test_review_round2.py`: the cancel seam, on scenes.
- Vitest: `FindingRow` renders the quote and a link with `book` and `scene`; "could not locate";
  an old `refs` finding shows its detail only.
- Browser journey (`scripts/e2e_browser.py`): run continuity → "Open in editor" → the editor URL
  has the finding's `scene=` and the scene text is shown.

## Docs (BRD / feature-set delta, for the orchestrator)

- `docs/BRD.md`, the continuity requirement (R1.1's continuity clause or the SPEC-02 entry): append
  "Continuity reads the book scene by scene. Each finding quotes the prose; the server finds the
  scene the quote is in and the report links to it, opening that scene in the editor. A
  finding whose quote cannot be found is kept and says 'could not locate' (SPEC-31)."
- `docs/BRD.md` R7.2: no change in wording; note under SPEC-08 resumption that a continuity check
  paused across this deploy restarts once (its input hash covers the chunk texts).
- `docs/feature-set.md`, "Continuity checking" row: "✅ Scene-chunked, quoted findings located to a
  scene; 'Open in editor' opens the scene (SPEC-31)". (The row's "empty cast" note is stale since
  SPEC-02.)
- `docs/specs/README.md`: add SPEC-31 to the index.
