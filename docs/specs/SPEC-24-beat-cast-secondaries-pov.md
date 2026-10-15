# SPEC-24 — Generation prompts: beat cast, named secondaries, POV

**Closes**: W2-1 (prompt half), W2-2 (prompt allowance + `suggested_characters`), W2-4
· **Size**: L · **Depends on**: the wave-2 day-0 contracts (`app/planning/planned_beat.py`,
the `secondaries.py` signatures) · **Wave**: 2 (package E2) · **Pairs with**: SPEC-23 (E1
persists the beats and passes the per-beat request), SPEC-27 (E5 renders the chips), SPEC-28
(E6 greps the P1–P7 strings).

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

### 24a — The outline asks for each beat's cast and allows named secondaries (P1, P2)

`app/planning/staged_outline.py`.

- `outline:chapters` and `outline:beats` carry `_cast_rules(canon)`:
  - `Principal characters MUST be canon cast: <cast>. …` — `pov` and `characters` name canon
    cast only; a secondary person goes in `secondary_names`.
  - **P1**, one line: `SECONDARY PEOPLE (named in the synopsis or canon, not in the cast; you
    may name them, never invent others): A, B, C`, or `… : (none)`. The list is
    `secondaries.secondary_candidates(canon)`.
  - The old sentence "Every name you use for a character MUST be one of" is gone.
- The chapters template adds `"secondary_names"`. The beats template (**P2**) gives each child
  `"characters"`, `"secondary_names"`, `"setting"` and `"tone"`, and the echo adds the chapter's
  `secondary_names`. The prose asks for the cast actually present in THAT beat, and says an
  unnamed functional figure stays unnamed. Acts are still numbered from 1. The stub and grader
  markers ("scene beats", "mirroring the chapters", "write 2-4 scene beats") are kept.
- `merge_structural` also carries `secondary_names` from S2 onto S3's nodes.
- Before validation, `keep_known_secondaries(nodes, canon_text, cast)` (generation path only):
  - drops a `secondary_names` entry that `canon_known_text(canon)` never names;
  - moves a `characters` entry that is not cast but is named in the canon text into
    `secondary_names`, so a model that cast Prava in `characters` still yields the suggestion.
- The fidelity audit runs on the raw nodes, so an invented secondary still raises the
  invented-names warning (`fidelity._outline_fields` reads `secondary_names`). A chapter's
  `characters` entry that the node also declares as secondary is not reported as "cast outside
  the canon".

### 24b — Validating beat children (§1.1)

`app/planning/outline.py::validate_outline_nodes`.

- Children keep `node_id`, `title`, `summary`, `children` as before, plus:
  - `character_ids` (optional): from `character_ids`, else `characters` names, resolved with
    `CastIndex` exactly like a chapter's. An unknown name is dropped (generation) or rejected
    (`strict`), **unless** the beat lists it in `secondary_names`. Absent when the model gave
    no cast.
  - `secondary_names` (optional): trimmed, deduped case-insensitively, ≤ 12
    (`planned_beat.MAX_SECONDARY_NAMES`), ≤ 100 chars. A name that resolves to the cast moves
    into `character_ids` (not duplicated).
  - `setting` (≤ 500) and `tone` (≤ 200), trimmed, kept when given as strings (even `""`).
- Top-level nodes add `secondary_names` with the same rules and the same `strict` exemption.
- A restore (`cast=None`) keeps stored ids and fields as they are: validation is byte-identical.

### 24c — Secondaries and suggestions

`app/planning/secondaries.py` (signatures frozen on day 0).

- `secondary_candidates(canon)`: capitalised runs in `canon_known_text(canon)` (synopsis
  paragraphs unwrapped; bible headings dropped and field labels stripped), in first-appearance
  order, excluding: cast names, aliases and any phrase built on a cast-name token ("Chief
  Executive Lich"); canon-entry names; the book title; ALL-CAPS headings and acronyms;
  title-case fragments; words the text also uses in lower case ("Intake", "Records" when used
  lowercase); single words that only ever open a sentence ("Death, it turns out"); and a
  shorter run inside a longer one ("Fen" when "Old Fen" is listed). On cel this lists Prava and
  Old Fen (plus some institutions: the heuristic cannot tell a person from a place, which is
  why it only feeds the prompt line).
- `suggested_characters(nodes, canon)`: only the names the model declared in a node's or a
  beat's `secondary_names`, that occur in the canon text and do not resolve to the current cast
  (`CastIndex`), deduped case-insensitively in outline order. `synopsis_sentence` is the first
  synopsis sentence naming them, else the first canon-entry sentence, capped at 400 chars at a
  word boundary (with "…"); `""` when only the bible names them. E1 exposes it (§1.2).

### 24d — The scene brief and the planner (P3, P4)

`app/orchestration/workflow.py`.

- `scene_block` (**P4**) omits the `Setting:` line when the setting is blank and the
  `Emotional tone:` line when the tone is blank **or** the literal `neutral` (the old request
  default; it carries no information and overrode the style guide's tone, so the guide now
  governs). When the request carries `secondary_names`, it adds one line
  `Also named (secondary, not in the cast): A, B`.
- `plan_scene_beats` (**P3**) offers `Available cast (not all need appear): A, B`, says a beat
  need not use all of them, allows the "Also named" people by name (never in a beat's
  `characters`), and allows `unnamed functional figures`. "cast is exactly" is gone.
  `parse_structured_beats` keeps the subset rule unchanged.

### 24e — POV (P5, P6, P7)

`app/orchestration/prose.py`.

- `resolve_pov(request, canon)` returns `"<mode>, <character>"` or `"<mode>"`:
  - mode: the request's `pov_mode`, else a mode in the request's `pov_character`, else a
    `(first|second|third)[ -]person( limited| omniscient| objective)?` match in `style.pov`,
    else `third person limited`;
  - character: the request's `pov_character`; else a name in `style.pov` that resolves to the
    cast ("third person limited, on Milo Voss" → Milo Voss; free text is never promoted).
- `write_beat_prose` renders it with `pov_instruction` (**P5**), one paragraph:
  `Point of view: third person limited, Milo Voss. Narrate only what Milo Voss perceives or
  knows: no aftermath, no foreshadowing, no naming people Milo Voss has not learned. …`
  With no character: `Point of view: <mode>. Narrate only what the point-of-view character …`.
- The last beat (**P6**): `Write ONLY beat N, the last beat: end the scene inside the moment,
  in POV; no summary of what follows.`
- **P7** markers are unchanged: `CHARACTERS IN THIS BEAT`, `BEAT TO WRITE`, `<beats>`, `<prior>`,
  `Stop where beat N+1 begins`. The `build_cast_context` call is unchanged (E4 owns the callee).
- `app/orchestration/revise.py` calls `resolve_pov` through `scene_pov`, so its
  `Point of view:` line now reads `<mode>, <character>` too (no change to revise.py).
- Ensemble mode builds its own prompts and is not covered (tpm-plan-wave2 §6 risk 4).

### 24f — The offline stub

`evals/stub_llm.py`. The beats branch (markers `scene beats` + `mirroring the chapters`) now
parses the act's chapters JSON and gives each child `characters` (its chapter's), `setting`
`"the intake atrium"` and `tone` `"wry"`; the first chapter of act 1's first beat declares every
name on the P1 line as `secondary_names` (E5's journey needs one "Add Prava to cast" chip). The
planner branch still parses the new `plan_beats` wording.

## Tests

- `tests/unit/test_beat_cast_prompts.py`: P1/P2 in the chapters and beats prompts (old
  sentence absent, principals rule present, markers kept); child validation (names → ids,
  secondary in cast moves, unknown dropped, `strict` rejects unless secondary, cap/trim,
  restore byte-identical, top-level secondaries); `keep_known_secondaries`; the staged outline
  end to end saves beat fields and warns on an invented secondary; `scene_block` P4 and the
  "Also named" line; the planner's P3 and the unchanged subset rule; the stub's beat fields
  (one secondary set only) and planner parsing.
- `tests/unit/test_secondaries.py`: cel candidates include Prava and Old Fen and exclude cast
  names, aliases, the title and headings; canon-entry and bible-label exclusion; suggestions
  exclude the current cast and anything absent from the canon text, dedupe in order, take the
  synopsis sentence (else a canon-entry sentence), cap at 400.
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
>   name people the synopsis or canon entries name who are not in the cast (listed in the
>   prompt as SECONDARY PEOPLE); it may not invent others, and `pov`/`characters` stay canon
>   cast. Each beat declares its `secondary_names`; a declared secondary the canon text never
>   names is dropped and warned about. The outline offers "Add <name> to cast" only for declared
>   secondaries that the canon text names and the cast lacks. *(SPEC-24)*

**docs/BRD.md, §5.1, after R1.1a:**

> - **R1.1b** A beat's scene is planned from its own cast: the planner sees the cast as
>   *available* (not all need appear; unnamed functional figures allowed), and a blank setting
>   or a blank/"neutral" tone is omitted from the prompt so the style guide governs. The prose
>   prompt states the POV mode and character and narrates only what that character perceives
>   or knows; the last beat ends inside the moment, with no summary of what follows.
>   *(SPEC-24)*

**docs/feature-set.md, add near "Staged outline":**

| Feature | BRD | Status | Where |
|---|---|---|---|
| Outline beats carry their own cast, setting, tone and named secondaries | R1.5a | ✅ | `app/planning/staged_outline.py`, `outline.py::validate_outline_nodes` (SPEC-24) |
| Named secondaries allowed in the outline; suggestions to add them to the cast | R1.5a | ✅ | `app/planning/secondaries.py` (SPEC-24; API SPEC-23, UI SPEC-27) |
| Planner treats the cast as available; no blank setting / neutral tone in prompts | R1.1b | ✅ | `app/orchestration/workflow.py` (SPEC-24) |
| POV line with mode + character; last beat ends in POV | R1.1b | ✅ | `app/orchestration/prose.py::resolve_pov`, `pov_instruction` (SPEC-24) |
