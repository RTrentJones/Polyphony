# SPEC-29 — Secondary characters sound like themselves

**Closes**: W3-1 (N3); review S6, Could 1 · **Size**: M · **Depends on**: the wave-3 day-0
markers (`SECONDARY_FENCE`, `INTRODUCED_LABEL`, `NAME_HEADING`, `VOICE_SAMPLES_LABEL`), SPEC-17
attribution, SPEC-24's `secondary_names`.

## Why

A named secondary reached the prompt as a name only (`Also named (secondary, not in the cast):
Prava`). The model invented a voice for them, and it landed on the nearest principal's register
(judge: "Fen's terse line shares Zara's register"). The author had already written Old Fen's
voice in the source; the prompt did not show it.

## Behaviour

`app/planning/secondaries.py`:

- `intro_sentence(name, texts) -> str` is the one "who is this" rule: the first sentence of
  `texts` that names them, capped at 400 characters, or `""`. `suggested_characters` uses it.
- `scene_secondaries(session, book_id, names, canon) -> list[Secondary]` reads the book's live
  sources once. Each source is attributed **once** (`character_extractor.attribute_speech`), and
  each name's lines come from `spoken_lines(..., cast=<canon cast>, attributed=<that source's
  result>)`. A tag that more than one cast member's names cover credits nobody, so a secondary
  never takes a principal's line. Lines shorter than `_SPEECH_MIN_WORDS` are dropped; lines of 4+
  words come first (one stable sort); the first 3 are kept. The introduction is looked up in
  `[synopsis, *canon entries, *sources]`.
- `render_secondaries(people) -> str`: one `<secondary>` fence; per person `### <name>`, then
  `Introduced: <sentence>` when found, then the voice-samples label with `- "<line>"` rows, or
  `NO_SAMPLES_NOTE` ("No samples: keep them brief and distinct from the cast."). No bible fields:
  the canon block carries them (BRD R1.1). `""` for nobody.

Where it goes. The block is built once per scene, in the session that loads the canon:

- prose workflow: the planner prompt (after the POV paragraph) and every beat prompt (right after
  the beat's cast context, inside CHARACTERS IN THIS BEAT);
- ensemble workflow: the planner only (the narrator and agent prompts are W3-10);
- revise: after the scene's cast context.

A secondary who resolves to a Character is cast by `beat_people` and gets the full character
block; it is never on the "Also named" line or in `<secondary>` (§2 item 5: the "(secondary)"
cast-block variant is not built). `_canon_hash` covers the block when it is non-empty, so a scene
paused across the deploy restarts once.

Cost: Prava and Old Fen on cel add ≤ 1,500 characters to a beat prompt.

## Tests

- `test_secondaries.py` (failing first): on the cel synopsis + excerpt as one source, Old Fen gets
  the synopsis sentence and "Nobody reads the appendices. That's what they're for."; Prava gets her
  sentence and `NO_SAMPLES_NOTE`; `attribute_speech` runs once for two names; the cost guard; a
  tag two cast members share credits no secondary.
- `test_prose_prompt.py`: the block follows the cast context and is absent without secondaries.
- `test_scene_canon.py` (guard, not failing-first): a secondary who is now a Character has its
  `### name` in the cast context and is absent from the Also-named line and from `<secondary>`,
  which carries the other secondary in every beat prompt.
- prompt_contract `secondary_has_context` (beat_prose): every name on the Also-named line has a
  `### name` in `<secondary>` with an `Introduced:` line or voice samples. A failing fixture is in
  `test_prompt_contract.py`; the round trip runs it on the app's real prompts.

## Docs delta (for the orchestrator)

**docs/BRD.md §5.1, after R1.5a:**

> - **R1.5b** A scene's named secondaries reach the planner, every beat prompt and revise with the
>   sentence that introduces them and up to three of their own lines from the book's sources (or
>   a note to keep them brief), never with a principal's line. *(SPEC-29)*

**docs/feature-set.md**, scene generation: "Named secondary characters speak in the voice the
author gave them in the sources."
