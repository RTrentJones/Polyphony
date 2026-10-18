# SPEC-70 — The voice header says only what is there (W9-5 (a))
**Closes**: W9-5 (a), S9 · **Size**: S · **Depends on**: SPEC-17 (the character block), SPEC-26.
- **Defect.** Beat prose and revise introduced the cast with "write (keep) each one's dialogue in THEIR OWN voice, matching
  their voice samples:" whatever the blocks held. W8b's prompts had no voice store, so the header claimed samples that no
  block carried (`w8b_empty_store`, beat_prose 0022).
- **Rule.** `app.characters.context.voice_clause(cast_context)`, beside `render_character`, is the one place the clause is
  chosen: "matching their voice samples" when `VOICE_SAMPLES_LABEL` appears in the rendered cast context, else "as the canon
  describes it". `write_beat_prose` passes the cast blocks with the secondaries; `build_revise_prompt` passes the same
  `voices` it prints (cast and secondaries). The literal "matching their voice samples" appears once in `app/`.
- **Unchanged.** A prompt with at least one voice sample is byte-identical to before: same words, same line break in beat
  prose, same `:\n\n` before the blocks. Only the clause changes for a cast with no sample.
- **Tests** (`tests/unit/test_prose_prompt.py`): (F) W8b's empty-store cast gets "as the canon describes it" in both
  prompts; (R) the same cast given a voice sample prints W8b's archived header byte for byte.
