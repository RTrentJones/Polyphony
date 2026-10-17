# SPEC-61 — Beat plans that quote the source, and the voice-overlap bound
**Closes**: W8-3 (b)(c), D7, D8 · **Size**: S · **Depends on**: SPEC-46, -52.
- **Source dialogue (S-6)** is cel's gold lines (train and test) plus every quoted span of the excerpt (attributed speech, approximated
  as quoted spans at day-0), as `folded6` 6-grams, once per run.
- `plan_quotes_source` (reported, Q1): the plan_beats beat lines sharing one of those 6-grams. Baseline **10 of 85** over W5d to W7br
  reproduces line for line. The product side is `workflow.DESCRIBE_NOT_QUOTE`, printed after plan_beats' POV line (D8, E2).
- **One stemmer** (`prompt_contract.stem` and `content_stems`, with its own stop list, not the product's `FUNCTION_WORDS`) serves
  `content_share`, `scripted_slots`, the revise step's `content_kept` and the bound below.
## The no-refill bound (`python -m evals.measure.voice_overlap <run-dirs>`)
Every shown slot whose stemmed share against its beat line is ≥ t is removed, with no refill; the grader's `voice_coverage` recomputes.

| Run | slots | ≥0.25 | ≥0.29 | coverage | bound t=0.2 | 0.25 | 0.29 | 0.33 | 0.5 |
|---|---|---|---|---|---|---|---|---|---|
| W5d / W5bd | 23 / 44 | 7 / 2 | 7 / 2 | 1.0 / 1.0 | 0.83 / 0.92 | 0.83 / 0.92 | 0.83 / 0.92 | 0.83 / 1.0 | 1.0 / 1.0 |
| W6 / W6b | 31 / 30 | 3 / 2 | 3 / 2 | 1.0 / 1.0 | 1.0 / 0.88 | 1.0 / 0.88 | 1.0 / 0.88 | 1.0 / 0.94 | 1.0 / 1.0 |
| W7 / W7b | 23 / 29 | 4 / 0 | 4 / 0 | 1.0 / 1.0 | 0.92 / 1.0 | 0.92 / 1.0 | 0.92 / 1.0 | 1.0 / 1.0 | 1.0 / 1.0 |
| W7r / W7br | 25 / 30 | 1 / 1 | 1 / 1 | 1.0 / 1.0 | 1.0 / 0.94 | 1.0 / 0.94 | 1.0 / 0.94 | 1.0 / 0.94 | 1.0 / 1.0 |

The lifted W7 0041 slot is 0.29 stemmed. Every run from W5d (W4b2 to W5bc predate SPEC-46) stays ≥ 0.8 at every threshold, so a 0.25
drop rule cannot take coverage below the gate. **Tests**: `test_prompt_contract.py` (10/85), `test_evals_measure.py` (two-block bound).
