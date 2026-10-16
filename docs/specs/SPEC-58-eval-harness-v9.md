# SPEC-58 — Eval harness v9: the history table, and `replayed` on empty objects

**Closes**: W7-6 · **Size**: S · **Depends on**: SPEC-52.

## Design
- **History (W7-6 (a); S-5: reported, never gated).** `evals/history.csv` (data) holds one row per gating draw since W4b2: `run`, `pair` (the wave's draw pair), redo RA, `untouched_share` (W5d and W5bd are the PM's own counts; earlier draws predate the field), the redo judge's rewording note, R2 mean and min, and per-character attribution where the run drew it. `report.history` renders the CSV, prints the act rule underneath, and lists the pairs where the rule fires. `evals.run` prints the table under the scorecard. **Act rule (PM):** if `untouched_share` < 0.5 or R2 mean < 0.90 in **both** draws of a wave, the item becomes P0 in the next wave. On today's rows it fires once, on R2 mean for pair 5 (W5 0.89, W5b 0.8875). A wave-5 pair, so it is history, not a wave-8 trigger.
- **`replayed` (D9).** It skips an answer whose JSON is empty: `[]`, `""`, `null`, or an object whose values are all empty **one level down** (`{"findings": []}`, `{"items": []}`). Nothing deeper is checked: `{"findings": [[]]}` counts. W6's continuity 1/2 becomes 0 on the regrade.
- **W7-1 (c), dracula aliases**: no harness change (S-2). The aliases are measurement inputs in E2's stop-gate script, SPEC-53 records the counts, and the dracula ground truth is unchanged.
- **The stub's map evidence** is the part's own sentences naming the character, so the round trip grades `evidence_verbatim` on quotes a real model would give.

**Tests**: `test_evals_graders.py` (the table renders from the checked-in CSV, and the act rule fires only on both draws of a pair) and `test_prompt_contract.py` (`replayed` rows).
