# SPEC-24 — Generation prompts: beat cast, named secondaries, POV

**Closes**: W2-1 (prompt half), W2-2 (prompt allowance + `suggested_characters`), W2-4
· **Size**: L · **Depends on**: the wave-2 day-0 contracts (`app/planning/planned_beat.py`,
the `secondaries.py` signatures) · **Wave**: 2 (package E2) · **Pairs with**: SPEC-23 (E1
persists the beats and passes the per-beat request), SPEC-27 (E5 renders the chips), SPEC-28
(E6 greps the P1–P7 strings).
**Wave 2.5 (package A, people):** 24a–24c and 24f are rewritten for §1e of tpm-plan-wave2_5:
one `people` list per chapter and beat, one `classify_people` call per node (review S2, S3,
S9, PR-S2, eval finding 3, W1-S7).

## Problem

- "Generate all planned scenes" wrote every scene with the whole chapter cast, a blank setting
  and a "neutral" tone. The outline never asked for a beat's own cast, setting or tone, and the
  scene planner was told "the scene's cast is exactly …", so a Milo-and-a-caseworker scene came
  back a Milo-and-Zara scene.
- The synopsis names Prava and Old Fen, but the outline was forbidden to name anyone outside
  the cast ("Every name you use for a character MUST be one of …"), so the author's secondary
  people became "the caseworker" and "an old Records clerk".
- Generated scenes opened or closed with an omniscient paragraph: the POV line said only
  "Point of view: Milo Voss" (or "as the book's style guide specifies (…)"), and the last beat
  was told to "end the scene here", which invited a wrap-up.

## Behaviour

### 24a — The outline asks for one `people` list per chapter and beat (wave 2.5, §1e)

`app/planning/staged_outline.py`.

- `outline:chapters` and `outline:beats` carry `_cast_rules(canon)`, three lines:
  1. `Principal characters MUST be canon cast: <cast>. Do not introduce new principal characters.`
  2. `People the synopsis or canon name who are not in the cast may appear by name; never invent others.`
  3. One sentence: `pov` names one canon cast member; `people` lists everyone named in that
     chapter or beat (canon cast, or people the synopsis or canon name).
- There is **no** list of secondary people in the prompt (wave-2 P1 is gone, review S3 / eval
  finding 2): its heuristic (`secondary_candidates`) listed mostly places. The model is asked;
  the server classifies.
- The chapters template is `{"title", "summary", "act_index", "pov", "people", "threads"}`. The
  beats template echoes those fields and gives each child
  `{"title", "summary", "people", "setting", "tone", "children": []}`. `characters` and
  `secondary_names` are no longer asked for. The prose still asks for the people of THAT beat
  and keeps an unnamed functional figure unnamed. The stub markers ("scene beats", "mirroring
  the chapters", "write 2-4 scene beats") are kept.
- `merge_structural`'s `_STRUCTURAL` is `("act_index", "pov", "people", "threads")`.
- `generate_staged_outline` builds `known_text = names.norm(canon_known_text(canon))` once per
  outline and passes it to `validate_outline_nodes`.
- The fidelity audit runs on the raw nodes. `fidelity._structural_cast` classifies each
  chapter's `pov` + people with the same `classify_people`; its "cast outside the canon"
  warning lists the `invented` entries plus a `pov` that does not resolve. There is no
  `secondary_names` exemption: a name the canon text names is simply a secondary.
  `flatten_outline(nodes) -> (titles, body)` is the one outline walk (recall reads both, the
  invented-names heuristic reads the body); it reads `people` and the legacy keys.
- `fidelity._norm` / `_present` are gone: recall and classification share `names.norm` /
  `names.mentions`, so "Voss," is a mention in both (review PR-S2).

### 24b — Validating people (§1e)

`app/planning/outline.py::validate_outline_nodes(nodes, *, cast, strict, known_text)`.

- A node's input refs are `people` + `character_ids` + `characters` + `secondary_names`, in that
  order, all optional. The legacy keys stay for stored outlines, restores, author PUTs and old
  archives. Each node (chapter or beat) makes **one** `classify_people(refs, cast, known_text)`
  call:

  | Path | `known_text` | invented |
  |---|---|---|
  | generation (`staged_outline`) | `names.norm(canon_known_text(canon))` | dropped (the audit warns) |
  | author edit (`strict=True`) | `None` (an unresolved name in `people` / `secondary_names` is a secondary) | rejected (422): a dead or foreign id, or a name placed in `character_ids` (that key holds ids; wave 2.5 W-2) |
  | restore (`cast=None`) | n/a | stored ids and names kept as they are |

- Stored node: `.ids` → `character_ids` (top level: the `pov` id first), `.secondary` →
  `secondary_names` (deduped case-insensitively, ≤ 12, ≤ 100 chars). `people` is never stored.
  `character_ids` is present when the input carried any people-ish list (possibly `[]`) or the
  pov resolved; `secondary_names` when it is non-empty or the input carried `people` or
  `secondary_names`.
- `pov` (top level) must resolve to the cast and is stored as the character's name; it is never
  a secondary. An unknown `pov` is dropped (generation) or rejected (`strict`).
- Children keep `setting` (≤ 500) and `tone` (≤ 200), trimmed, when given as strings.
- A restore and a re-validation of stored nodes are byte-identical.
- **Removal trigger** (PR-S8.1): the legacy input keys `characters` / `secondary_names` go once no
  stored `book_plans.content` has a `characters` key (one read-only prod query, a user action;
  tpm-plan-wave2_5 §4).

### 24c — Suggestions

`app/planning/secondaries.py` keeps only `suggested_characters(names, canon)`.

- `names` are the `secondary_names` the outline and its promoted chapters declared (collected
  by `app/api/plans.py`). They are re-classified with `classify_people` against canon's
  current cast and `names.norm(canon_known_text(canon))`; the `secondary` ones are offered,
  deduped case-insensitively, in the given order. `synopsis_sentence` is the first synopsis
  sentence naming them, else the first canon-entry sentence, capped at 400 chars at a word
  boundary (with "…"); `""` when only the bible names them.
- Suggestions come only from model-declared people. A canon phrase that is a place ("Aeon
  Holdings") is offered if the model declares it a person (tpm-plan-wave2_5 §7 risk 2); the fix
  is the prompt, never a heuristic.

### 24d — The scene brief and the planner (P3, P4)

`app/orchestration/workflow.py`.

- `scene_block` (**P4**) omits the `Setting:` line when the setting is blank and the
  `Emotional tone:` line when the tone is blank only (§24e: an author's `neutral` is a choice
  and is printed; a blank tone lets the style guide govern). When the request carries `secondary_names`, it adds one line
  `Also named (secondary, not in the cast): A, B`.
- `plan_scene_beats` (**P3**) offers `Available cast (not all need appear): A, B`, says a beat
  need not use all of them, allows the "Also named" people by name (never in a beat's
  `characters`), and allows `unnamed functional figures`. "cast is exactly" is gone.
  `parse_structured_beats` keeps the subset rule unchanged.

### 24e — POV (P5, P6, P7)

`app/orchestration/pov.py` (moved from `prose.py` in wave 3; see "Wave 3" below).

- POV is a typed value (wave 2.5, review S6): `Pov(mode: str, who: str | None)`, a NamedTuple;
  `Pov.label` is `"<mode>, <who>"` or `"<mode>"`. Nothing joins it into a string and re-splits it
  (`split_pov` and the unread `scene_request["pov_mode"]` are gone).
- `resolve_pov(request, canon) -> Pov`:
  - mode: a `(first|second|third)[ -]person( limited| omniscient| objective)?` match in
    `style.pov`, else `third person limited`. A character name is never searched for a mode;
  - who: the request's `pov_character` (the API writes the chapter POV's cast name); else the
    first capitalised run in `style.pov` (outside the mode) that `CastIndex.resolve` maps to the
    cast, dropping leading words until it does ("on Dr. Ada Vance" → Ada Vance, "first person,
    Milo" → Milo Voss). Free text is never promoted.
- `write_beat_prose(..., pov: Pov)` renders it with `pov_instruction(pov)` (**P5**), one paragraph:
  `Point of view: third person limited, Milo Voss. Narrate only what Milo Voss perceives or
  knows: no aftermath, no foreshadowing, no naming people Milo Voss has not learned. …`
  With no character: `Point of view: <mode>. Narrate only what the point-of-view character …`.
- The last beat (**P6**): `Write ONLY beat N, the last beat: end the scene inside the moment,
  in POV; no summary of what follows.`
- **P7** markers are unchanged: `CHARACTERS IN THIS BEAT`, `BEAT TO WRITE`, `<beats>`, `<prior>`,
  `Stop where beat N+1 begins`. The `build_cast_context` call is unchanged (E4 owns the callee).
- The revise job calls `resolve_pov` directly (`revise.scene_pov` is gone); the revise prompt
  renders `pov_instruction(pov)` (wave 3, below).
- **Tone (wave 2.5, review S7).** `scene_block` omits `Emotional tone:` only when the tone is
  blank. A tone the author chose, `neutral` included, is printed: the modal no longer defaults to
  `neutral` (SPEC-25), so a `neutral` that reaches the backend is a deliberate choice.
- **Style section (wave 2.5, W1-S3).** `_style_section` no longer embeds the sample prose in a
  `<sample_prose>` fence or repeats the tone: both are in the canon block's style guide
  (`render_style`). With sample prose it says the sample "(the canon's style guide, above) is your
  primary style reference"; with a tone alone it says to follow that tone; the generic craft text
  still appears only when the book has neither. A beat prompt carries the sample exactly once.
- Ensemble mode builds its own prompts and is not covered (tpm-plan-wave2 §6 risk 4).

**Wave 3 (W3-3; review B-1, S4).** The beat plan was written with no POV at all, and revise
printed only the label:

- `Pov.one_head` is the one wide-mode rule: every mode but omniscient and objective is held to one
  head. `pov_instruction`, `plan_pov_instruction` and the grader's `check_pov_line` all read it.
- The one-head name rule is "no naming people before <who> learns their name in the story; an
  introduction in this scene counts" (no more name badges). The wide modes drop "after this beat",
  which means nothing to the planner or to a multi-beat revise: omniscient is "The narrator may
  move between characters' thoughts: no aftermath, no foreshadowing."
- `plan_pov_instruction(pov)` is `pov_instruction(pov)` plus, when `one_head`, "Plan only what
  <who> perceives. If a beat needs a person named, plan the moment <who> learns the name (an
  introduction, a nameplate, a form)." `plan_scene_beats` takes the resolved `Pov` and prints it
  after the scene brief; the ensemble planner passes `resolve_pov` too.
- `build_revise_prompt` prints `pov_instruction(pov)`, limit included, instead of the bare label,
  and every revise says "Keep the events, and the facts, people and objects the surrounding text
  depends on." (no redo-only branch).
- `parse_beats` is deleted: a non-JSON planner answer gives one whole-scene beat and logs
  `plan_beats_unparsed`. The `last_beat_no_wrap_up` tripwire is deleted;
  `pov_line_has_mode_and_character` now also grades `plan_beats` and `scene_revise`.
- Tests: `test_pov_prompt.py` (`one_head`, the name rule, no "after this beat"),
  `test_workflow.py` (a limited plan names its POV character and the learn-the-name sentence; an
  omniscient plan has no "Plan only what"; the non-JSON answer), `test_scene_revise.py` (the POV
  paragraph and the amended sentence).

### 24f — The offline stub

`evals/stub_llm.py`. The chapters branch gives each chapter `people` (the cast it carries). The
beats branch (markers `scene beats` + `mirroring the chapters`) parses the act's chapters JSON
and gives each child `people` (its chapter's), `setting` `"the intake atrium"` and `tone`
`"wry"`. The first chapter of act 1's first beat also names every `<synopsis>` name matching
`\b([A-Z][a-z]+), the ` that is not a cast-name token ("Prava, the intake caseworker"), so the
server keeps Prava as a secondary and the e2e journey still gets its "Add Prava to cast" chip.
Routing stays a single text-sniffing path. The planner branch still parses `plan_beats`.

## Tests

- `tests/unit/test_beat_cast_prompts.py`: both outline prompts ask for `people`, carry the §1e
  sentence and no SECONDARY PEOPLE line (exact wording is graded by SPEC-28's round trip); child
  validation (legacy keys → ids, a secondary in the cast moves, cap/trim, restore
  byte-identical); `strict` keeps Prava and Elara secondary and rejects a dead id, a name in
  `character_ids` (`test_outline_handoff.py`) or an unknown pov; generation with `people: ["Milo", "Prava", "Elara"]` gives Milo's id, `["Prava"]`, Elara
  dropped, and legacy `characters` + `secondary_names` give the same; the staged outline end to
  end saves beat fields and warns on an invention; `scene_block` and the planner; the stub's
  `people` and Prava on the first beat only.
- `tests/unit/test_staged_outline.py`: Halvard Bell (eval finding 3) in a beat's `people`, named
  by the synopsis, is stored as a secondary with no invented-name warning.
- `tests/unit/test_fidelity_gate.py`: `principal_recall` finds a punctuated alias ("Voss,");
  `flatten_outline` returns (titles, body) from one walk over `people`, threads and children.
- `tests/unit/test_secondaries.py`: suggestions exclude the current cast and anything absent
  from the canon text, dedupe in order, come only from declared people (a declared "Aeon
  Holdings" is offered), take the synopsis sentence (else a canon-entry sentence), cap at 400.
- `tests/unit/test_pov_prompt.py`: request POV + `style.pov` "third person limited, on Milo
  Voss" → `Point of view: third person limited, Milo Voss.` then `Narrate only what Milo Voss
  perceives or knows`; mode precedence; no style → `third person limited`; style-guide character
  only when cast; last beat has P6; P7 markers kept.
- `tests/unit/test_scene_canon.py`: the workflow's beat prompt carries the style guide's mode
  and its cast POV character; the last beat carries P6.
- `scripts/e2e_stub.sh` passes, so the stub markers survive.

## BRD / feature-set delta (for the orchestrator to apply)

**docs/BRD.md, §5.1, after R1.5:**

> - **R1.5a** **Named secondaries are allowed; principals are still gated.** The outline may
>   name people the synopsis or canon entries name who are not in the cast; it may not invent
>   others, and `pov` stays canon cast. Each chapter and beat lists its `people`; the server
>   classifies them once (cast → `character_ids`, named by the canon text → `secondary_names`,
>   anyone else is dropped from the saved outline and reported by the invented-names warning).
>   The outline offers "Add <name> to cast" only for declared secondaries that the canon text
>   names and the cast lacks. *(SPEC-24, SPEC-23)*

**docs/BRD.md, §5.1, R1.1c (already applied; the earlier draft called it R1.1b):**

> - **R1.1c** A beat's scene is planned from its own cast: the planner sees the cast as
>   *available* (not all need appear; unnamed functional figures allowed), and a blank setting
>   or a blank tone is omitted from the prompt so the style guide governs. The prose
>   prompt states the POV mode and character and narrates only what that character perceives
>   or knows; the last beat ends inside the moment, with no summary of what follows.
>   *(SPEC-24)*

**docs/feature-set.md, add near "Staged outline":**

| Feature | BRD | Status | Where |
|---|---|---|---|
| Outline beats carry their own cast, setting, tone and named secondaries | R1.5a | ✅ | `app/planning/staged_outline.py`, `outline.py::validate_outline_nodes` (SPEC-24) |
| Named secondaries allowed in the outline; suggestions to add them to the cast | R1.5a | ✅ | `app/planning/secondaries.py` (SPEC-24; API SPEC-23, UI SPEC-27) |
| Planner treats the cast as available; no blank setting / neutral tone in prompts | R1.1c | ✅ | `app/orchestration/workflow.py` (SPEC-24) |
| POV line with mode + character; last beat ends in POV | R1.1c | ✅ | `app/orchestration/prose.py::resolve_pov`, `pov_instruction` (SPEC-24) |

**docs/BRD.md, §5.1, R1.1c, wave 3 (append):**

> The beat planner and revise carry the same POV paragraph: the plan stays in the POV
> character's head and has them learn a name before the prose uses it; a wide (omniscient,
> objective) mode is not held to one head. *(SPEC-24 wave 3)*
