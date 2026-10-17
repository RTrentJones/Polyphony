# SPEC-60 — Attribution is a reported number over ten draws
**Closes**: W8-2 (a)(c), D3, D6 · **Size**: S · **Depends on**: SPEC-33, -55.
- `ATTRIBUTION_DRAWS = 10` with draw-indexed memo keys. Per character the report gives `correct`, `k`,
  `accuracy` and `wilson95` (`evals/measure/wilson.py`), and the step carries `status: "reported"`. Nothing
  on model output is gated. The fixed-input gate stays SPEC-55's probe rows.
- run-W8 runs `attribution` **last**, after revise (S-3), so its extra probe calls cannot defer a scene.
- `evals/history.csv` gains `tighten_ra_1..3`. `report.history` prints attribution pooled from W7 onward
  with its interval: today Milo 4/6 (0.30, 0.90), Zara 3/6 (0.19, 0.81), Edric 3/6 (0.19, 0.81).
## The lines each threshold shows (D6; W7r's retained store, `polyphony_w7r`)
`python -m evals.measure.lines_shown eval-attr-cel "Zara Okafor" "Edric Thane"` calls the product's
`character_samples` with the probe's own arguments (k = 3, a scene of one). It has no retrieval logic.

| Speaker | threshold 0 (today) | threshold 0.2 (wave 6) |
|---|---|---|
| Zara | 1 "Don't thank her. Read the appendices first…"; 2 "Close the accounts in your queue…"; 3 "It was the best week of your death, too…" | 1 only |
| Edric | 1 "Every clerk likes a clean ledger…"; 2 "Mr. Voss. Do sit…"; 3 "You closed the Bell account in under four minutes…" | 1 only |

At 0.2 each speaker sees only their top line; Edric's is the one the classifier reads as Milo (SPEC-55).
An accuracy delta between thresholds must be read against this. The threshold stays 0.
**Tests**: `test_evals_measure.py` (Wilson rows, `lines_shown` at both thresholds), `test_evals_steps.py`
(k = 10, `status`), `test_evals_graders.py` (history columns, pooled line, tighten act rule).
