# SPEC-54 — Signed gold placements and verbatim evidence (eval harness v8)

**Closes**: W7-2, W7-4 (b) · **Size**: S/M · **Depends on**: SPEC-45, -52, -56.

## Design
- **The signed file is the only placement a gate reads** (D4). `evals/placements/<run>.json` (schema in `evals/README.md`) is the PM's reading of the run's outline. `plot.placements` returns the file whose `outline_hash` (the parsed outline) is the run's and whose `signed_by` is set, else `None`; a signed file missing an event's cell is an error. The report carries `outline_hash` and `placement_signed`.
- **Unsigned → `null`** (S-4): `samples_not_plot` (with `classified` and `later_plot_slots`) and `movement` are `None`, and so is `unplaced_events`. There are no provisional numbers and no fallback to the draft.
- **`unplaced_events`** are the events the signer set to `null` (a span without terms, `training`, is never listed).
- **The draft** (`plot.draft_placements`, `python -m evals.graders.plot draft <answered-dir> --run W7`) is the term match with word boundaries: a term matches at a word start and ends at a word end or an inflection (`s`, `es`, `ed`, `ing`, `y`, `ies`), so "memo" never matches "memory", while "summon" still matches "Summoned". It writes an unsigned file. It feeds only the CLI and `placement_disagreements` (every cell where the draft and the signed file differ; `None` unsigned). These are reported, not failed.
- **`evidence_verbatim`** (check 23, extraction_reduce): each `EVIDENCE_ITEM` quote must be a run of the uploaded manuscript (synopsis + excerpt), after whitespace is collapsed, every quote mark is folded to one, and the outer marks are ignored. The extractor's stand-in "narrates part N" is a mark, not a quote. The check reports `lines` {n, verbatim}. It reads the prompt and the corpus only, never the repair rule or the fixture's labels. Baseline: W5d 18/28, W5bd 12/12, W6 22/28, W6b 8/12.

## Regrade (nine archives; before = day-0 substring match, after = the PM-signed files)
| Archive | Signed cells ≠ draft | `samples_not_plot` (rate, n, classified, later) | movement (n, ok, listing) | `unplaced_events` |
|---|---|---|---|---|
| W6 | corner office null → 2 | 1.0, 10, 37, 0 = | 0, ok, Milo [0,3,9,13,27] Zara [3,29] = | corner office → — |
| W6b | bell 4 → 2; drawer 4 → null; corner office null → 6; discrepancy null → 2 | 1.0, 14, 34, 0 = | as W6 = | corner office, discrepancy → drawer |
| W5 | drawer 4 → 2 (draft: summons 1 → 2, "memory") | 1.0, 10, 21, 0 = | 0, fails ("none indexed") = | — = |
| W5d | none | 1.0, 10, 25, 0 = | as W6 = | — = |
| W5bd | drawer 3 → null; summons 4 → 5; corner office 5 → 6 | 1.0, 18, 47, 0 = | as W6 = | — → drawer |
| W4b2 | discrepancy 4 → 2 | 0.0667, 15, 77, 37 = | 0, ok, Milo [0,13] Zara [29] = | drawer = |
| W5b | none | 1.0, 19, 0 (vacuous), 0 = | 0, fails ("none indexed") = | drawer = |
| W5c | none | 1.0, 10, 15, 0 = | as W6 = | — = |
| W5bc | discrepancy 3 → 2 | 1.0, 18, 27, 0 = | as W6 = | drawer = |

`=` means unchanged. **No gated value moves.** Every moved cell lies at or past its run's chapter-1 gold tail (bell, paragraph 9; in W6 the summons, 14), so neither the later-plot cut nor the D3a listing can see it. `movement_coverage` is unchanged in all nine. The only changes are in `unplaced_events`, and each follows from a cell the PM signed. W6 and W6b still pass. W4b2, W5, W5b, W5c and W5bc are compared, not re-judged (their sample blocks were defective).

**Tests**: `test_prompt_contract.py` (unsigned → null against a draft that would place; a draft file is not a signature; a missing cell raises; word-boundary rows; signed nulls and disagreements; evidence on the fixture lines and stand-in) and the round trip (unsigned and signed variants, 23 checks).
