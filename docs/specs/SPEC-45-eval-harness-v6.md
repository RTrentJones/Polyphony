# SPEC-45 — Eval harness v6: an author's revision sequence, and plot graded from gold

**Closes**: W5-6, the graders of W5-1 (d)(e) and W5-3 (d) · **Size**: S/M · **Depends on**: SPEC-39, -40, -41, -42.

## Design
- **Gold** (`cel/ground_truth.json`, PM-approved): `excerpt_events`, each with its first excerpt paragraph (`split("\n\n")`, 0-based), the plan-line terms that name it and its `narrated` paragraphs: intake 0, training 2-8 (no terms: never placed, so Zara's training lines are manner), bell 9, drawer 13, summons 14, corner office 17, discrepancy 27. `owned_phrases`: Edric's "Mr. Voss" (spans of 2+ words; not 'one').
- **`evals/graders/plot.py`** imports no product rule. `locate`: an event's chapter is that of the first scene line in the run's latest outline:beats answers (per act) containing a term. A line places one event, unless every line naming it is taken. `paragraph_of`: the first paragraph holding each clause of a sample.
- **`samples_not_plot`** (beat_prose, scene_revise, gated 1.0). Part 1: a movement sample from a `narrated` paragraph of a placed event. Part 2 (needs "chapter N of M"): a voice sample from a later-placed event's span, or a movement sample narrating one; each counts in `later_plot_slots`. Reports `classified`. The cast block is "IN THIS BEAT", or the revise prompt's "IN THIS SCENE".
- **`emptied_by_rule`** (D6): an empty principal block is emptied when every movement line its owner is shown anywhere in the run is plot there; it leaves `movement_coverage`, and `emptied_blocks` lists each block with its plot paragraphs.
- **`borrowed_phrase`** on prose and revise rows (reported): an owned phrase in quotes while its owner is not cast. **`replayed`**: `prompt_contract <dir> --against <archives>` gives, by purpose, the share of answers byte-identical to an answer in the other archives; a prompt repeated within the run counts once.
- **Sequential revise.** The tighten is the last paragraph inside beat 1's located prose (the last paragraph when the scene has fewer than two beats). It is accepted through `POST .../revisions/{id}/accept`, and `accepted` is true only if the re-read draft equals the proposal. "Colder" and "Redo beat 2: let Milo notice one thing about the room that unsettles him." (no keep clause, B-4) are then asked of the accepted draft and discarded. A 409 is recorded `refused` with no score. Revise rows copy `dropped_callbacks`.
- The prose report's `voice.speech` counts each character's dialogue chunks of ≥ 3 words (Edric, D9). `PROSE_CACHE_KEY` is `planned-v6`.

## Baselines (regrade of the archives)
| Archive | prompts | failing | part 1 | `later_plot_slots` | coverage excl. emptied (emptied) |
|---|---|---|---|---|---|
| W4 | 10 | 10 | 10 (0021-0024, 0026-0029, revise 0030, 0031) | 14 | 1.0 (0) |
| W4b | 18 | 15 | 13 | 24 | 0.941 (5)¹ |
| W4b2 | 15 | 14 | 3 (0021, 0022, revise 0025) | 37 | 0.933 (0) |

¹ Four "Locked Drawer" blocks and the "Bell Account" one: Milo is only shown paragraphs 0 and 13. 0.889 under wave 4's brief-only reading (principal re-check 2).

**Tests**: `test_prompt_contract.py` (both parts, `locate`, C1, revise block, D6), `test_evals_graders.py` (`borrowed_phrase`, `replayed`), `test_evals_steps.py` (tighten in beat 1, accept, 409 `refused`, `speech`), `test_evals_client.py`, `test_evals_corpus.py` (spans), and the round trip's plot fixture.
