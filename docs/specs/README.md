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
| [SPEC-10](SPEC-10-outline-to-scene-handoff.md) | Outline → chapter → scene handoff | F9 | M | SPEC-01 | ✅ |
| [SPEC-11](SPEC-11-voice-workbench.md) | Voice workbench and grounding provenance | F15 (G4), F18 | M | SPEC-01 | ✅ |
| [SPEC-12](SPEC-12-cost-and-usage-surfaces.md) | Cost before action, and usage surfaces | F15 (R7.5), F17 (silent downgrade) | M | SPEC-03, SPEC-07 | ✅ |
| [SPEC-13](SPEC-13-versioning-completeness.md) | Versioning completeness and a trash | F16 | M | SPEC-04 | ✅ |
| [SPEC-14](SPEC-14-ensemble-v2.md) | Ensemble v2 | F17 | L | SPEC-01, SPEC-03, SPEC-08, SPEC-12 | ✅ |
| [SPEC-15](SPEC-15-hygiene.md) | Hygiene and catalogue reconciliation | F18, F19, F20 | S–M | — | ✅ (15c shim removal pending prod confirmation) |
| [SPEC-16](SPEC-16-editor-safety.md) | Editor safety net: unsaved-changes guard, local draft autosave, modal revision preview | B1, B13 (editor) | S | — | ✅ |
| [SPEC-17](SPEC-17-voice-attribution.md) | Voice samples are the character's own words | B2 | M | SPEC-09, SPEC-11, SPEC-14 | ✅ |
| [SPEC-18](SPEC-18-beat-prose-and-outline-beats.md) | Beat-level prose prompt and outline beats prompt | B5, B11, relay prior-tail finding | M | — | ✅ |
| [SPEC-19](SPEC-19-scene-revise.md) | AI revision of a scene (backend) | B3 (API + job) | L | SPEC-07, SPEC-08, SPEC-12 | ✅ (editor UI: SPEC-25) |
| [SPEC-20](SPEC-20-book-page-phone-and-export.md) | Book page at phone width, and the export dialog | B4, B9 (UI) | S | SPEC-21 | ✅ |
| [SPEC-21](SPEC-21-manuscript-hygiene.md) | Manuscript hygiene: clean export, honest outline warnings, upload types | B9 (backend), B14, relay invented-names finding | S | — | ✅ (promote-time title cleanup: SPEC-23) |
| [SPEC-22](SPEC-22-eval-harness-v2.md) | Eval harness v2: an eval that tests the product | eval plan §3.5 fixes 1–8 | M | — | ✅ |
| [SPEC-23](SPEC-23-planned-beats-as-data.md) | Planned beats as data | W2-1 (backend), B9 (promote title), B7 (backend) | L | SPEC-10, SPEC-12, SPEC-13 | ✅ (editable beat list: SPEC-35) |
| [SPEC-24](SPEC-24-beat-cast-secondaries-pov.md) | Generation prompts: beat cast, named secondaries, POV | W2-1 (prompts), W2-2, W2-4 | L | wave-2 day-0 contracts | ✅ (prose mode; ensemble prompts not covered) |
| [SPEC-25](SPEC-25-revise-ui.md) | Revise UI, and planned beats read as data in the frontend | W2-3 (B3 UI), W2-1 (frontend) | M+ | SPEC-16, SPEC-19, SPEC-23 | ✅ |
| [SPEC-26](SPEC-26-character-bible-extraction.md) | Extraction writes a real character bible; beat prompts stop repeating it | W2-5, W2-12 | M | SPEC-09, SPEC-17 | ✅ |
| [SPEC-27](SPEC-27-secondary-cast-and-guided-start.md) | Suggested cast, the guided empty book, and the wave-2 journey | W2-2 (UI), W2-10 / B12 | M+ | SPEC-23, SPEC-24 | ✅ |
| [SPEC-28](SPEC-28-eval-harness-v3.md) | Eval harness v3: fair caps, a consistent fixture, contracts for wave 2 | W2-11 (H1–H3, F8, F7) | M | wave-2 day-0 contracts | ✅ |
| [SPEC-29](SPEC-29-secondary-voice.md) | Secondary characters sound like themselves | W3-1 | M | wave-3 day-0 markers, SPEC-17, SPEC-24 | ✅ |
| [SPEC-30](SPEC-30-story-position.md) | Early scenes do not know the ending (story position, movement samples) | W3-2 | S/M | wave-3 day-0 markers, `Chapter.number` | ✅ |
| [SPEC-31](SPEC-31-located-continuity.md) | Located continuity: each finding quotes the prose and opens it | W3-4 (B10 / W2-8 core) | M | SPEC-02 | ✅ (quote not selected in the editor; mark-fixed is W3-11) |
| [SPEC-32](SPEC-32-chapter-rows-and-generate.md) | Chapter rows are unambiguous, and Generate is never a dead end | W3-6 (B12 rest) | M | SPEC-23, SPEC-24 | ✅ |
| [SPEC-33](SPEC-33-eval-harness-v4.md) | Eval harness v4: honest denominators, one name rule | W3-7 | M | wave-3 day-0 contracts | ✅ |
| [SPEC-34](SPEC-34-partial-planned-batch.md) | The planned batch writes what fits | W4-1 (NO path) | M | SPEC-12, SPEC-23 | ✅ (cached tokens not measured) |
| [SPEC-35](SPEC-35-chapter-and-beat-editor.md) | Edit the chapter and its planned beats before you generate | W4-2 | M | wave-4 day-0 contracts | ✅ (beat reorder cut) |
| [SPEC-36](SPEC-36-manner-not-material.md) | Draft in the author's voice, not the author's sentences | W4-3 | S/M | wave-4 day-0 labels, `SceneScope.brief` | ✅ |
| [SPEC-37](SPEC-37-scene-identity.md) | Scenes have names, and the editor knows where you are | W4-4 | M | wave-4 day-0 contracts | ✅ |
| [SPEC-38](SPEC-38-outline-history.md) | An outline you can go back to, whose acts see the whole book | W4-5 | S/M | wave-4 day-0 contracts | ✅ |
| [SPEC-39](SPEC-39-eval-harness-v5.md) | Eval harness v5: grade the path authors use | W4-6 | S/M | SPEC-33, SPEC-34 | ✅ |

SPEC-01 to SPEC-15 close review findings (`F…`). SPEC-16 to SPEC-22 are wave 1 of the 2026-10
cycle: they close product-backlog items (`B…`) and findings from the relay eval run, and SPEC-22
merged first so the "before" and "after" evals share one harness. SPEC-23 to SPEC-28 are wave 2
(backlog items `W2-…`); SPEC-28 re-scored the after-wave-1 run as wave 2's "before". SPEC-29 to
SPEC-33 are wave 3 (backlog items `W3-…`). Wave 3 also amended two earlier specs in place: SPEC-17
(one voice retrieval and renderer, and the voice probe, W3-5) and SPEC-24 (the POV paragraph in the
planner and revise, W3-3).

SPEC-34 to SPEC-39 are wave 4 (backlog items `W4-…`), built on one day-0 contract commit (markers,
`Chapter.number`, `written_beats`, the scene brief and the wave-4 API types); their BRD and
feature-set edits landed with the wave. Wave 4 also amended SPEC-22 (no fallback scene, up to five
judged scenes) and SPEC-23 (`scene_batch.written_beats`) in place.

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
