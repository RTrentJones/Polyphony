# SPEC-39 — Eval harness v5: grade the path authors use

**Closes**: W4-6 (M3, M4, M5, M7, M15, M16, M17) · **Size**: S/M · **Depends on**: SPEC-33, SPEC-34.

## Why
Seven open misses were measurement, not product. The fallback scene was the worst: in run-W3 it
graded one unplanned scene in a new last chapter, which hid the very refusal SPEC-34 fixes.

## Design
- **No fallback.** `_fallback_scene` and its settings are deleted. When no promoted chapter can be
  written, prose reports `path: "failed"`, the reason and a `None` score. The failure is caught
  outside the cache's `memo`, so it is never cached and the next run tries again. The run stays
  at the default budget; the harness waits for `skipped + queued` scenes.
- **Judge every planned scene, up to five.** `EVAL_PROSE_JUDGE_SCENES` is deleted.
- **Per-role probes.** `voice_probe` is `{character: probe}` in every corpus. In cel, Edric
  reviews the quarter's numbers and Milo and Zara are reviewed. Each attribution row reports its
  probe.
- **Whole gold utterances.** A quote broken by its speech tag is one gold line, joined as
  `_join_utterance` joins it; no utterance has halves in both `train` and `test`.
- **The outline book's secondaries** are committed as `minor` characters, each described by
  `intro_sentence` over the excerpt, then the synopsis (the synopsis never names Bell). The prose
  book does not commit them.
- **`prior_paragraphs`.** A `<prior>` whose first 40 squashed characters equal the answer of a
  beat-1 prompt (`→ marks beat 1,`) is the scene's own opening and passes whatever it starts with.
- **App rule (product, M5).** `_candidates` skips a capitalised run followed by a label token (a
  single capital, a number or a code: "Appendix F", "Form 9", "Desk R-14"). "Our" is a function
  word, and a capitalised function word mid-sentence opens a title ("a slide called Our Values"),
  so it is kept and the run reads as a title fragment. `revise.new_names(revised, draft, canon)`
  checks against `canon_known_text(canon)` plus the draft, the outline audit's known text, so
  "God help him" in the synopsis is not an invention.

**Tests**: `test_evals_steps.py` (failed path not memoised; judge 4 of 4, 5 of 7; Edric's probe;
minor secondaries), `test_evals_client.py`, `test_evals_corpus.py`, `test_prompt_contract.py`,
`test_fidelity_gate.py`, `test_scene_revise.py`.
