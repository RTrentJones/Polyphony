# Wave 21 and W21-C analysis outputs (2026-11-19)

The one analysis run each of SPEC-96 and SPEC-97 made, kept here because workflow artifacts expire.

- `w21_days.json` and `w21c_days.json`: the `--days` files (run id to ledger day label). Day 30 is not in the W21 file;
  it was dispatched after the stop (SPEC-96, Result).
- `m1_validation.json`: `evals.narration.contract validate dracula frankenstein wuthering`, script only, passed as
  `--m1-validation`.
- `w21-analysis-9d6cf5c.*`: `evals.narration.analyze run` from the strict harness on the chapter, Sonnet and strict
  reports.
- `w21-analysis-ebed0aa-no-strict.*`: the signed analyzer on the same reports less the strict judge's; Amendment 2's
  equality check compares its `family`, `primary`, `decisions`, `floors` and `sensitivities` with the file above.
- `w21c-analysis-ebed0aa.*`: the signed analyzer on W21-C's reports. SPEC-97 reads only its P line's d, p, t-interval
  and cluster count; the tool's header, Holm values and decision lines are SPEC-96's wording (they say Flash-Lite-written
  and "proposed for the next product wave") and are disregarded, as SPEC-97 pre-registered.
- `arm_means.json`: each arm's per-cluster mean and t-interval for the person and tense measures over the P pairs,
  which SPEC-97's room rule reads.
