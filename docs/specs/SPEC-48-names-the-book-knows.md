# SPEC-48 — Drafted prose names only people the book knows, and narrates no hindsight

**Closes**: W6-3 (a)(b) · **Size**: S/M · **Depends on**: wave-6 day-0 (`name_warnings` field and API, `SceneSetup.position`).

## Design
- **One naming rule (D10).** `NAMING_RULE` in `pov.py`: "Name only people in the canon, the cast or 'Also named'. Anyone else stays unnamed: 'a man from Procurement'." `write_beat_prose` and `build_revise_prompt` print it on the line after `pov_instruction`. Revise's older "Do not introduce named characters…" sentence is deleted, so the rule is stated once.
- **Hindsight (D10).** `pov_instruction` names the example inside its one rule, in both branches: "no foreshadowing — never 'he would later learn'". The planner gets it too, through `plan_pov_instruction`. `pov.py` stays the one place that rules on what the POV may tell.
- **The scene's name warning (D8).** `scene_name_warnings(prose, canon, scene_request, earlier)` calls `unknown_proper_nouns` directly. Known: the canon terms plus the request's Also-named people; known text: the canon's text (`canon_known_text`) plus the text before the scene (`prior_scene_tail`). Revise's `new_names` calls the same function with the draft as known text; neither wraps the other.
- **Stored at generation.** Prose and ensemble completion write the list into `evaluation_scores.name_warnings` (the day-0 merge form); `GET /scenes/{id}` returns it. Institutions stay listed (wave-4 policy). Warn-only: generation never blocks on it.
- **Editor.** When the list is non-empty, the scene editor shows, in RevisePanel's warning style: "When it was generated, this scene named people or places not in the canon (may be inventions): Gary, Procurement". The wording says *when it was generated* because the stored list goes stale after edits (risk 2).

## Fixed-input rows (`tests/fixtures/w5_archives.json`, `name_warnings`)
| Scene | Warned | Not listed |
|---|---|---|
| W5d ch. 1 scene 1 (`0021`–`0024`) | Gary, Procurement | Prava (a canon secondary) |
| W5c ch. 1 scene 1 (`0021`–`0024`) | Derek, Procurement | Prava |
| Unit text (neither archive names him) | — | Halvard Bell (canon), an Also-named person |

## Notes
- The `hindsight` and `prose_named_offpage` reports are E1's (W6-3 (c)); model output is reported, not gated.
- No new prompt marker: nothing parses the naming line, so it stays in its emitter.
