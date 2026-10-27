"""W19 S7 (SPEC-94, D19-9): validate RN and RC on intact and damaged chapters.

- `build`: the blinded item set (intact source chapters and damaged variants)
  and its key, deterministically from `chapters.json` and a seed.
- `step`: the `rnrc_validation` eval step, judging each item with RN and RC
  through the pipeline's own input builders and the run's judge chain.
- `analyze`: per damage type, how often the damaged variant scores below the
  intact one (aggregates only).
- `export`: a sample for S4's human calibration page.
- `rn2` (SPEC-95 E2): RN-v2, the narrator forced choice on the same chapters,
  its own opt-in step `rn2_validation`, items, key and analysis.
"""
