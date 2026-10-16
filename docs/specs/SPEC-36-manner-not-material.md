# SPEC-36 — Draft in the author's voice, not the author's sentences

**Closes**: W4-3 (M12, M10, M6); review B-1 · **Size**: S/M · **Depends on**: the wave-4 day-0
labels (`VOICE_SAMPLES_LABEL`, `MOVEMENT_LABEL`: "for manner only") and `SceneScope.brief`.

## The event rule

`context.restates_brief(text, brief)` is the one rule: a movement sample that shares
`RESTATES_MIN = 4` content words (`[a-z]+`, longer than 3 letters, not `fidelity.FUNCTION_WORDS`)
with the brief narrates the event being written, which the brief already carries. It is dropped
in `character_samples` beside `names_outside_scene`, with no refill (SPEC-30). Voice samples are
never filtered by it.

- **The brief only, at 4 — not the backlog's "brief or beat line, ≥ 5".** `query` is a plan line
  in prose and the ensemble, but the author's selected prose in revise; against a ~200-word span,
  6 of 41 movement samples share ≥ 5 words, pure gestures included. In the run-W3 archive's 11
  distinct (brief, sample) pairs the two event pairs share 6 (the Drawer) and 5 (intake), every
  other pair ≤ 2, so 4 sits midway.
- `scope.brief` is the scene request's `scene_description`, the same in prose, ensemble and revise.
  A voice probe or a manual scene has `""`, which makes the rule inert.
- **Thin canons.** A dropped sample is not replaced, so the Drawer beat has no Milo movement line.
  `movement_coverage ≥ 0.8` is the eval's guard against starvation (it compounds W-6). Residual
  risk, measured in run-W4b: the cel store holds two Milo movement samples, so all four Drawer
  beats had an empty Milo block and coverage fell to 0.727 (0.889 without those blocks). Wave 5
  reports blocks the brief emptied separately from coverage.

## POV

A one-head `pov_instruction` ends: "The brief may say what other people intend or feel; show only
what {who} can see or hear." (M6). Wide modes do not carry it.

## Grader

`sample_lift` (beat_prose): the answer shares no word 8-gram (`seams.grams`) with the voice samples
or movement lines in its own prompt. It reports `max_shared` and `share_of_beats`. There is no
`movement_not_brief` check: it would import the product rule and pass by construction; the (F)
table of real pairs pins the rule and `sample_lift` measures the outcome.
