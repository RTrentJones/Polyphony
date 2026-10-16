# SPEC-34 — The planned batch writes what fits

**Closes**: W4-1 (NO path) · **Size**: M · **Depends on**: SPEC-12 (estimates), SPEC-23 (planned beats).

## Why
In run-W3 every promoted chapter's "Generate all planned scenes" was refused ("Needs about 172,424
tokens; 126,223 remain"): the preflight refused the whole chapter when any scene did not fit.

## Design
- **One partial rule.** `estimates.affordable(per_scene, remaining)` is how many scenes, in beat
  order, fit the rolling 24-hour budget. The batch queues that prefix and defers the rest. It is
  refused (429, the estimate's message) only when not even the first scene fits: the endpoint
  assesses the first `max(fit, 1)` scenes, so there is one refusal path.
- **Every return says what was deferred.** The new batch, the in-flight batch and
  `nothing_to_do` all carry `queued` (this chain's length), `deferred` and `reason`. `reason` is
  non-null exactly when `deferred > 0`:
  "k of n scenes fit your budget now; generate again as your 24-hour budget frees up to write the
  other d (written scenes are skipped)."
- **Click again, no auto-continuation.** The budget is a rolling 24 hours: a deferred scene may fit
  minutes after the chain ends, or not for a day. A later click skips the written beats; an
  automatic resume would be a second scheduling path holding the batch key for a day.
- **Ensemble batches** stay all-or-nothing on provider requests (`assess`'s request check); the
  partial rule counts tokens only.
- The toast appends the server's `reason`; the row label is SPEC-35's. `_EXPECTED_BEATS = 5`
  stays: overestimating now defers a scene instead of refusing a chapter.
- The eval's prose step reports `queued`, `deferred` and `estimate_over_actual` (the batch
  estimate over the ledger's `tokens_used_24h` delta; target ≤ 1.3) at the default budget.
- **Cached tokens: not measured** (no free-tier key in the build environment). The spike and
  W4-1's cached-token weighting are wave-5 items for the owner. BRD R1.1 is not raised.

**Tests**: `test_estimates.py`, `test_handoff_followups.py` (3 of 4 queued; a 429 below one
scene; aged usage frees the fourth; `deferred: 0` elsewhere), the button's Vitest, the journey.
