# SPEC-62 — The gold `narrated` at D1 grain, and `movement.ok` that is the gate
**Closes**: W8-4 (b)(c)(d), D4, D10, G1 · **Size**: S · **Depends on**: SPEC-45, -54.
- **G1 (PM, `1abf6d5`):** intake [0], summons [14], corner office [17, 19], discrepancy [27]. The cells
  dropped are p1, p15, p20 and p28. Reasons are in SIGNING-NOTES, "Wave-8 narrated re-sign".
- **D4:** `movement.ok` holds when every principal cast in a chapter-1 beat block is eligible somewhere,
  and at least 0.8 of the eligible blocks move. n = 0 fails. Principals with no chapter-1 block are
  listed in `not_cast`, and ineligible principals in `not_eligible`.
## D10: thirteen archives through `plot.regrade` (each with its own report's store)

| Run | samples_not_plot (classified) | movement n/passed | ok day-0 → wave 8 |
|---|---|---|---|
| W4b2, W5, W5b | 0.067 (77), 1.0 (21), 1.0 (0) | null: no movement_store | null |
| W5c, W5bc, W5d, W5bd, W6, W6b | 1.0 (15, 27, 25, 47, 37, 34) | 0/0 | true → **false**¹ |
| W7, W7b | 1.0 (31, 38) | 9/0, 12/1 | false → false |
| W7r, W7br | 1.0 (48, 51) | 8/8, 12/12 | true → true |

**G1 moves nothing** (values, failures, `ineligible`, `voice_coverage` identical): no shown sample sits
in p1/p15/p20/p28; the p15 (Milo) and p20 (Edric) store lines (W7–W7br) change no eligibility, as Milo
is already eligible and Edric is `not cast` in every stored run. ¹ D4, not G1: Milo and Zara are cast
in chapter 1 with every stored line plot there; the old rule passed on the listing alone.
**Tests**: `test_prompt_contract.py` (W7 false; Milo moving, true; Edric `not cast`; W5 rows rewired), round trip.
