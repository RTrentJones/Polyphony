# SPEC-30 — Early scenes do not know the ending

**Closes**: W3-2 (N1 + N2; the story-position half here, the movement half in "Movement
samples"); review S5, R-3, B-4, Could 3, Could 6 · **Size**: S/M · **Depends on**: the wave-3
day-0 `STORY_POSITION` marker and `Chapter.number`.

## Why

Every prompt carries the whole-book synopsis (BRD R1.1) and then says "no foreshadowing". The
model cannot tell what has happened by chapter one, so it hinted at later events ("pre-empts next
chapter's 'write under' beat").

## Story position

`app/orchestration/workflow.py`:

- `StoryPosition(chapter, of)`: `Chapter.number` and the book's live chapter count.
- `story_position_line(position)`: `This scene is in chapter N of M. The synopsis tells the whole
  book; nothing after this chapter has happened yet. Do not mention, hint at or set up later
  events.` `""` for no position.
- `chapter_context(summary, position)`: the `<chapter>` fence, then that line right after it. The
  planner, the beat prompt and the revise prompt print it in their stable prefix.

`app/orchestration/prose.py`:

- `scene_chapter(session, scene) -> (summary, StoryPosition | None)` is the one loader for both
  scene jobs and revise (S5): `("", None)` without a chapter; `(summary, None)` for a **trashed**
  chapter (R-3: it keeps an old position the live count no longer covers, so "chapter 5 of 4"
  cannot be printed); else the live chapter's position.
- `scene_prior_tail(session, scene)` is the other half of the old `handlers._scene_inputs`, which
  is deleted with its payload fallback (dead: every scene job is created in a chapter).
- Both scene handlers read the two at job start (SPEC-08). The ensemble workflow passes the
  position to its planner only.
- `_canon_hash` covers the position line when there is one: a chapter moved during a pause
  restarts the scene rather than mixing positions.

**Invariant (Could 6).** `Chapter.number` (`position + 1`) is the backend's one "chapter N" rule.
Live chapter positions are contiguous from 0 because every delete, move and restore renumbers them
(`renumber_two_phase`), so it equals the export's `enumerate(…, 1)` numbering. A trashed chapter's
number is meaningless, which is why `scene_chapter` gives it no position.

## Movement samples

A movement sample (under `MOVEMENT_LABEL`, "How they move (gesture and habit only; do not reuse these events, objects or names):") is kept only when (1) it is not first-person narration, (2) `_centres` holds against the scene's cast (`SceneScope.cast`), and (3) `context.names_outside_scene` is empty. That rule names (a) any other book-cast member outside the scene, matched by `_forms`/`_names_in` (name forms exclude `NON_NAME_TOKENS`; one-word forms count only when capitalised), and (b) any capitalised name `unknown_proper_nouns` finds against the canon terms, the in-scene name forms and the scene's `secondary_names`. A filtered block shows what is left, with no refill and no unfiltered fallback. A canon-known non-cast name (Prava, in the synopsis) is not caught. Residual risks: a sentence-initial "Will" or "Rose" still names a character of that name; and on a book with a thin canon (a one-paragraph synopsis), rule (b) drops samples that name an office, a street or a firm, and can empty a movement block. The prompt_contract check `movement_names_in_scene` grades it on ground truth (principals plus `secondary_cast`): a person is in the scene through a `###` block or the Also-named line, and a principal also when the scene brief or beat plan names them; a secondary is in the scene only through the Also-named line. The eval reports `movement_lines` per principal, and `movement_coverage` (principal blocks with at least one movement line) must be at least 0.8 in the eval.

## Tests

- `test_prose_prompt.py` (failing first): the position line follows `</chapter>` in the beat
  prompt; `scene_chapter` gives `StoryPosition(2, 5)` for the second of five live chapters with
  one trashed, `(summary, None)` for a trashed chapter and `("", None)` without one.
- `test_workflow.py`: the line follows `</chapter>` in the planner prompt.
- `test_scene_revise.py`: the revise prompt carries it.
- prompt_contract `story_position_line` (beat_prose, plan_beats, scene_revise with a `<chapter>`):
  exactly one `STORY_POSITION_RE` match with 1 ≤ N ≤ M. The round trip grades the value
  `scene_chapter` computed for its fixture (`1 of 2`), asserted before use (B-4).

## Docs delta (for the orchestrator)

**docs/BRD.md §5.1, after R1.1c:**

> - **R1.1d** The planner, beat and revise prompts say where the scene sits ("chapter N of M")
>   and that nothing after this chapter has happened yet; the synopsis stays whole. *(SPEC-30)*
