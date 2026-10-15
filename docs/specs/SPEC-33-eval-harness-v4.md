# SPEC-33 — Eval harness v4: honest denominators, one name rule

**Closes**: W3-7 (N10, N11, the harness halves of N6 and N7) and the `classify_sample` line of
W3-1 · **Size**: M · **Depends on**: the wave-3 day-0 contracts (`continuity.Finding`'s `quote`).

## Why

Three of the five weakest run-W25 numbers were measurement errors, not product errors:

- extraction precision 0.5 counted the author's real secondaries (Prava, Old Fen, Halvard Bell)
  as spurious;
- the outline's `unknown_canon` reported a person the manuscript names (Halvard Bell) and a sign
  written in capitals (INITIAL);
- `mention_order_ok` was false on counts that were correct (Milo 33, Zara 21, Edric 37).

The continuity control was graded against a fact sheet whose synopsis was "continuity eval". The
attribution `margin` is 0 whenever the attribution is right, so it hid how close the voices are
(Milo 0.1907 against Edric 0.1878). The dialogue probe was a knock at a door, which no one in the
corpus would hear.

## Outcome

Every eval number fails only for something an author would also call wrong.

## Design

### The app rule (N10, product)

`fidelity._PROPER` has no all-caps branch: a token written entirely in capitals (a sign or a
stamp: INITIAL HERE, NOW SERVING, I SURVIVED Q3, SEE APPENDIX M, DISPOSITION PENDING) is never a
candidate, so it is never reported. "Bartholomew Quince" still is. This changes the author-facing
outline warning ("may be inventions") and revise's `new_names`, as well as the eval.

### One secondary list (ground truth)

- `ground_truth.json` gains `secondary_cast: ["Prava", "Old Fen", "Halvard Bell"]` (the people
  the manuscript names who are not the principals) and `voice_probe` (cel: "It is the end of the
  quarter in Reconciliations and your numbers are under review. Say a few sentences to the
  reviewer."). Dracula and Frankenstein carry their own in-world probes.
- `fidelity.secondaries` is deleted; `Prava` and `Old Fen` leave `fidelity.known` ("Fen" stays).
- `known_names(gt)` = `fidelity.known` ∪ `secondary_cast`: the known set for `_unknown_canon`
  and `grade_fidelity` (live and offline).
- The outline's synopsis secondaries are `secondary_cast` filtered by `named_in(synopsis, …)`
  (cel: Prava and Old Fen; Halvard Bell is in the excerpt only).
- The eval does **not** commit extraction's default selection into the prose and outline books:
  that would cast Prava and Old Fen, and W3-1 would have nothing to measure. Halvard Bell in the
  outline's `app_warnings` is reported, not graded.

### Steps and graders

- **Extraction.** `grade_extraction(predicted, gold, secondary=())`: precision = (principal +
  secondary hits) / predictions; recall stays over the principals; `secondary_found` lists the
  secondary hits. Run-W25's six names give precision 1.0.
- **Mentions.** `mention_order_ok` is replaced by `principal_mentions_min`, the fewest name
  mentions of any principal (target ≥ 10; a principal with no proposal counts 0). The report labels
  it "name mentions". Pronouns are not counted.
- **Continuity book.** Each run (`injected`, `control`) is a `_book_with_cast` book: the corpus
  synopsis, cast and style, plus one chapter summary, `canonical_beats[0]["title"]` (the corpus
  has no chapter summaries; the excerpt is beat 1). `_finding_matches` reads `detail` + `quote`;
  `_finding_quote` reads `quote` only (SPEC-31); nothing reads `refs`.
- **Attribution.** `AttributionResult.gap` = top-1 score − top-2 score; `accuracy()` adds
  `mean_gap`; `margin` is kept for misses. Run-W25's Milo line has gap 0.0029.
- **Probe.** `_KNOCK` is deleted; `step_attribution` sends `ground_truth["voice_probe"]`, which is
  also in the memo key, so old answers are not reused for the new probe.
- **`classify_sample`.** A sample is `own` when it matches `gold[owner] + other[owner]`: a
  secondary's lines live in `other_lines` (cel: Old Fen), so the secondary samples W3-1 adds grade
  as their own.

## Tests

`test_fidelity_gate.py` (the all-caps signs), `test_evals_graders.py` (secondary precision, gap),
`test_evals_steps.py` (`principal_mentions_min`, the continuity books' synopsis and summary,
`_unknown_canon` and Halvard Bell, `classify_sample` and Old Fen, the probe). All failed on the
day-0 tree first.

## Docs delta (for the orchestrator)

- `docs/feature-set.md`, outline fidelity warning: "Words written entirely in capitals (signs,
  stamps) are never reported as possible inventions."
- `docs/BRD.md` §7 (evals): extraction precision counts the corpus' secondary cast; the mention
  check is `principal_mentions_min` ≥ 10; attribution reports `gap` / `mean_gap`; the continuity
  control is graded against the corpus canon.
