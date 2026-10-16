# SPEC-41 — Redo beat keeps what the rest of the chapter refers to

**Closes**: W5-2 · **Size**: S/M · **Depends on**: wave-5 day-0 (`SceneScope.plot`, `beats_block`).

## Design
- **Every revise** reads the scene's `SceneBeat` lines (by `beat_number`) and adds them to `scope.plot`, so a movement sample that restates one of the scene's own beats is dropped (SPEC-40, B-1).
- **A redo** (`beat_index` set) prints, after the POV line: "Beat N of this scene's plan: …", the whole plan through `beats_block` with `→` on beat N, "Keep these (the rest of this chapter refers to them): …" when `keep` is non-empty, and "If the instruction does not say what to change, keep the beat's events and facts and change only the telling." `build_revise_prompt(beat=(index, lines), keep=…)`: the "Beat N" line and the block read one list. A range or whole-scene revise prints none of it.
- **`callbacks(target, rest, canon)`** is the one rule. `rest` is the scene's prior and after text plus the drafts of the chapter's live completed scenes with a higher position (trashed ones are not read). A term is a callback when `rest` also uses it (whole word, any case; a dash-led fragment by its word). Never a canon term (`canon_terms()`, or a capital the canon's text names), never a capitalised word that opens a sentence, never "one".
- **Warning.** A `keep` term the revised span no longer uses adds `DROPPED_CALLBACK` ("This version drops 'nine', 'six', which the rest of this chapter refers to.") to the proposal's `warnings` and to `meta["dropped_callbacks"]` (always a list; `[]` off a redo). Warn-only; the eval reports it, never gates it.
- **Grader.** `redo_sees_plan`: a scene_revise prompt with "Beat N of this scene's plan" carries `<beats>` with `→` on item N.

## Classes and the example that pins each
| Class | Pinned by |
|---|---|
| Number words and digits | W4 "Retained" beat 2 (`0022`) → scene 2 (`0029`): "nine", "six" |
| Hyphenated compounds | W4b2 "Mid-Sentence" beat 2 (`0026`): "B-flat" |
| Dash-led fragments | W4b2 `0026`: "—list" (the unfinished sentence; `<after>` says "list") |
| Capitalised, not opening a sentence | W3's "Appendix F" (`0027`'s `<after>`), unit fixture |

## Archived lists (`tests/fixtures/w4_redos.json`)
| Redo | `callbacks` | The archived answer drops |
|---|---|---|
| W4 `0031` | two, Forty-one, nine, six | two, nine, six (the warning) |
| W4b2 `0026` | —list, B-flat | nothing |

## Notes
- `rest` includes the prior: "B-flat" occurs only in `0026`'s `<prior>` ("hummed in B-flat"), so a rule on after-text and later scenes alone misses it.
- "two" is a residual false positive on W4 (the target's "two fingers", the after's "two words"); only "one" is excluded, as planned.
