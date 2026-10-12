# Polyphony — Deliverable specs

These specs turn the findings in [`docs/REVIEW-2026-10.md`](../REVIEW-2026-10.md) into units of
work that can each ship as one PR (or a short stack). They sit under the [BRD](../BRD.md), not
beside it: a spec that changes product behaviour lists the BRD edits it carries, and per the
`polyphony-canon` skill those edits land in the same PR as the code.

## How to use a spec

Each spec has the same sections: **Why** (the findings it closes), **Outcome** (what is true when
it ships), **Scope** (in and out), **Design** (the intended approach, with file pointers),
**Acceptance** (checkable statements, each of which should map to a test or an eval), **Docs**
(BRD / feature-set / skill edits), **Depends on**, and **Size** (S ≈ a day, M ≈ two to four days,
L ≈ a week or more of focused work).

The design sections are a recommended approach, not a contract. If implementation finds a better
route, change the spec in the same PR so it stays true. The acceptance criteria are the contract.

## Index

| Spec | Title | Closes | Size | Depends on | Status |
|---|---|---|---|---|---|
| [SPEC-01](SPEC-01-canon-grounded-scenes.md) | Canon-grounded, cast-by-ID scene generation | F1, F10 | M | — | ✅ |
| [SPEC-02](SPEC-02-continuity-v2.md) | Continuity v2: whole canon, no silent cuts, honest status | F2 | M | — | ✅ |
| [SPEC-03](SPEC-03-quota-safe-llm-and-tier.md) | Quota-safe LLM client and a tier that owns its limits | F3, F4 | M | — | ✅ |
| [SPEC-04](SPEC-04-book-rooted-scenes.md) | Scenes are book-rooted | F6 | M | — | ✅ |
| [SPEC-05](SPEC-05-real-client-rate-limits.md) | Rate limits keyed on the real client and the user | F7 | S | — | ✅ |
| [SPEC-06](SPEC-06-eval-parity-and-fidelity-gate.md) | Eval parity with the product, and the fidelity gate in CI | F5 | M | — | ✅ |
| [SPEC-07](SPEC-07-job-lifecycle.md) | Job lifecycle: visible pauses, bounded waits, cancel, idempotency | F11, F13, F14 | M | SPEC-03 | ✅ |
| [SPEC-08](SPEC-08-incremental-resume.md) | Incremental resume for multi-call jobs | F12 | M | SPEC-07 | ✅ |
| [SPEC-09](SPEC-09-extraction-at-manuscript-scale.md) | Extraction at manuscript scale | F8 | L | SPEC-03, SPEC-07 | ✅ |
| [SPEC-10](SPEC-10-outline-to-scene-handoff.md) | Outline → chapter → scene handoff | F9 | M | SPEC-01 | 🚧 |
| [SPEC-11](SPEC-11-voice-workbench.md) | Voice workbench and grounding provenance | F15 (G4), F18 | M | SPEC-01 | ✅ |
| [SPEC-12](SPEC-12-cost-and-usage-surfaces.md) | Cost before action, and usage surfaces | F15 (R7.5), F17 (silent downgrade) | M | SPEC-03, SPEC-07 | ✅ |
| [SPEC-13](SPEC-13-versioning-completeness.md) | Versioning completeness and a trash | F16 | M | SPEC-04 | ✅ |
| [SPEC-14](SPEC-14-ensemble-v2.md) | Ensemble v2 | F17 | L | SPEC-01, SPEC-03, SPEC-08, SPEC-12 | ✅ |
| [SPEC-15](SPEC-15-hygiene.md) | Hygiene and catalogue reconciliation | F18, F19, F20 | S–M | — | ✅ (15c shim removal pending prod confirmation) |

## Sequencing

```
            ┌───────────── SPEC-06 eval parity (first: makes the rest measurable)
            │
 P0 ────────┼── SPEC-01 scenes ──┬── SPEC-10 handoff
            │                    ├── SPEC-11 voice workbench
            ├── SPEC-02 continuity
            ├── SPEC-03 LLM/tier ──── SPEC-07 jobs ──┬── SPEC-08 resume ──┐
            │                                         ├── SPEC-09 extraction
            │                                         └── SPEC-12 cost ───┼── SPEC-14 ensemble v2
            ├── SPEC-04 book-rooted scenes ── SPEC-13 versioning          │
            └── SPEC-05 rate limits                                       │
                                                                          │
 SPEC-15 hygiene: any time, in small slices ──────────────────────────────┘
```

**Milestone 1 — "The invariants hold everywhere, and CI can tell"**: SPEC-06, then SPEC-01 to
SPEC-05. Exit test: the BRD §7 acceptance test passes in CI on the `cel` corpus, a forced quota
storm of ten consecutive 429s pauses every job and loses none, and no prompt-assembly path in
`app/` contains a slice of author content.

**Milestone 2 — "Real books, real budgets"**: SPEC-07, SPEC-08, SPEC-09, SPEC-10. Exit test: a
90k-word manuscript uploads, extracts into reviewable canon within the free tier over one or more
days without a failed job, and an outline promoted to chapters lets an author generate a scene
without retyping a single name.

**Milestone 3 — "The author can see and steer"**: SPEC-11, SPEC-12, SPEC-13. Exit test: every
G4 requirement is reachable from the UI, every expensive button shows its estimate, and every
deletion is recoverable from a trash view.

**Milestone 4 — "Polyphony"**: SPEC-14, after the requests-per-day decision in BRD §6.1.

## Cross-cutting rules for every spec

- **Prompt assembly goes through `Canon`.** New generators take a `Canon` (or a narrower,
  explicitly scoped view of one) and fence untrusted content with `as_quoted_block` under
  `STORY_MATERIAL_NOTICE`. A string slice of author content in a prompt is a review blocker.
- **Every LLM-spending endpoint is a job** unless it is a bounded, single, fast-model call with a
  timeout below the proxy limit. Every job kind registers `on_dead` and `on_pause`.
- **Every expensive enqueue preflights** against the remaining budget and returns an estimate.
- **Every acceptance criterion names its test.** Prefer deterministic tests with a stubbed LLM
  client (the pattern in `tests/unit/test_staged_outline.py`); add an eval only when the property
  depends on model behaviour.
- **Update `docs/feature-set.md`** status rows in the same PR.
