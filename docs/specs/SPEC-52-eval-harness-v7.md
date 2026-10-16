# SPEC-52 — Eval harness v7: measure what wave 6 changes, and stop measuring noise

**Closes**: W6-7, W6-5 (b), the graders and reports of W6-1 (d), W6-2 (d), W6-3 (c), W6-4 (d) · **Size**: S/M · **Depends on**: SPEC-45, -51.

## Design
- **`unplaced_events`** (W6-7 (b)): the gold events with terms that no plan line places (`plot.unplaced`; `training` has no terms and is never placed). Listed per run in `samples_not_plot` and in the prose step, and **read before any plot gate**. Gold is unchanged: the PM's Q2 ruling keeps `partition` `narrated: [3]`.
- **`movement`** (D3a, replaces D6's `emptied_by_rule` / `every_block_emptied`): a principal is **eligible** in a beat when the store (`voice.movement`, else the lines shown in the run) holds a line of theirs that is neither part-1 plot nor at or past the gold tail for the scene's chapter (`plot.only_plot`). Each eligible principal's block must carry a movement line; `n` counts the beats with one. **n = 0 passes only with every principal listed with the gold paragraphs that make them ineligible** (`ineligible`, `ok`); one listed with no paragraph (an empty store) fails. `movement_coverage` is now the plain share of principal blocks that move (reported).
- **Attribution** (D13): k = 3 draws per character (`ATTRIBUTION_DRAWS`), memo key `char|probe|draw`. Accuracy is over all draws; `per_character` gives each character's correct count and majority. Gate: Milo and Zara majority-correct; Edric reported.
- **Tighten**: the longest paragraph of beat 1 with ≥ 30 words, else the scene's longest such; else the row records `{"id": "tighten", "error": …}`.
- **`replayed`** (D12): an answer that is missing or an empty JSON value (`[]`, `{}`, `""`, `null`) is not counted. The within-run exclusion is unchanged.
- **Minor cast** (W6-5 (b), hygiene, not gated): committed synopsis-first through `introduction` (Prava: "There is Prava, the intake caseworker who onboarded Milo with such practiced warmth"). `OUTLINE_CACHE_KEY` is `minor-v3`; `PROSE_CACHE_KEY` `planned-v8` (scenes record `name_warnings`).
- **Reported, never gated**: `hindsight` (answer sentences with "would later", "much later", "years later", "would come to", "did not yet know"); `untouched_share` per revision (the target's sentences kept verbatim); `scripted_slots` (each voice slot's pronoun-folded 6-run hit and content-word share with its beat line and brief; B-4, not a check); the prose rows' `name_warnings` beside `unknown_canon`; the outline's `app_warnings`, `people_mismatches` and `title_reuse`.

## Baselines (offline regrade; the store from each report's `voice.movement`)
| Archive | `unplaced_events` | movement | `hindsight` | folded-6 hits |
|---|---|---|---|---|
| W4b2 | drawer | n = 0; Milo [0, 13], Zara [29] (shown lines) | 0010 | 0 / 50 |
| W5 | — | n = 0, **fails**: no store recorded, nobody has a gold reason | — | 3 / 17 |
| W5c | — | n = 0; Milo [0, 3, 9, 13, 27], Zara [3, 29] | — | 3 / 12 |
| W5d | — | as W5c | 0022 | 1 / 23 |
| W5bd | — | as W5c (Edric is in no beat) | — | 0 / 44 |
| W5bc | drawer | as W5c | 0025 | 0 / 23 |

The seven folded-6 hits are the seven `drop: true` rows of `w5_archives.json`. The paragraph lists vary with each draw's placements; the gate is the listing, not these numbers.

**Edric** (Could 5, measured offline before any change): at threshold 0.2 his probe is shown one sample, "Every clerk likes a clean ledger…", which the classifier itself attributes to Milo (margin 0.41); his two Edric-like lines (0.19, 0.17) fall below it. Pooled draws: 1 / 4 (W4's hit by 0.0003). A probe-only `score_threshold=0` is a wave-7 proposal, to be measured with three relay draws first.

**Tests**: `test_prompt_contract.py` (unplaced on the pre-"next desk" gold; the W5d / W5bd movement table; an eligible empty block; hindsight rows; scripted table), `test_evals_graders.py` (`replayed`), `test_evals_steps.py` (draws, tighten, minor intro, reports), the round trip (Zara listed with paragraph 3).
