# SPEC-14 — Ensemble v2

**Closes**: F17 · **Size**: L · **Depends on**: SPEC-01, SPEC-03 (RPD decision), SPEC-08,
SPEC-12 · **Gate**: do not start until BRD §6.1's requests-per-day question is answered with
measured figures (SPEC-03 step 3) and a decision on whether ensemble is paid-only.

## Why

The ensemble loop is the product's namesake and is close to its spec, with four gaps. R6.2 (action
grounding retrieved separately from dialogue) is not implemented. The chapter summary is accepted
and ignored. The narrator receives a 600-character slice of the prior tail, the last surviving
silent cut on a generation path. There is no preflight or estimate, and nothing measures whether
ensemble scenes are better than prose scenes, which is the claim that justifies their cost.

## Outcome

Character agents are grounded separately on how they speak and how they move; the narrator and
editor see the chapter's intent; nothing is sliced; the cost is estimated and enforced before the
job starts; and an eval shows whether ensemble beats prose on voice distinctness and action
coverage, so the free/paid gating is a measured decision.

## Scope

**In**: `app/orchestration/ensemble.py`, retrieval by chunk type, ensemble estimator, an ensemble
eval step.
**Out**: concurrent agent calls (pacing makes them pointless on the free tier).

## Design

1. **Split grounding (R6.2).** `build_character_context` gains a `mode` parameter. Proposal agents
   receive two fenced blocks: `<voice>` from `retrieve_similar(chunk_type="dialogue")` and
   `<physicality>` from `chunk_type in ("action", "description")`, each with its own `k`, each
   falling back to untyped retrieval when its type has fewer than two chunks (the ingest classifier
   under-labels, per the audit). Requires SPEC-09's classifier to be passable; measure label counts
   on the corpora first.
2. **Chapter intent.** The narrator and editor prompts include the chapter summary and, when
   SPEC-10 has landed, the chapter's planned beats, fenced as `<chapter>`.
3. **No slices.** Replace `clean_for_llm(prior_tail)[:600]` with the same `PREV_TAIL_WORDS` window
   the prose path uses, fenced as `<prior>`.
4. **Preflight.** The SPEC-12 estimator for ensemble computes `beats × (agents + 2) × rounds`
   calls and the token cost of the narrator and editor carrying the full canon. The enqueue refuses
   with the estimate when either the token budget or the provider's remaining daily requests cannot
   cover it.
5. **Checkpointing.** Per SPEC-08, checkpoint after each agent proposal, narration and review, so a
   pause mid-round never re-spends completed agent calls.
6. **Eval.** An `ensemble` eval step generates the same two-character scene in prose and ensemble
   modes from the Dracula corpus and grades both with the attribution grader (generated-line voice
   distinctness), an action-coverage check (every present character has a non-dialogue sentence
   naming them), and the judge. Report the delta and the call count.

## Acceptance

1. A proposal prompt contains a `<voice>` block of dialogue chunks and a `<physicality>` block of
   action or description chunks for a character that has both. *(unit)*
2. The narrator prompt contains the chapter summary and no slice of the prior tail. *(unit + the
   SPEC-06 prompt-content gate)*
3. An ensemble request whose estimate exceeds the remaining RPD returns 429 with the estimate.
   *(unit)*
4. The ensemble eval step runs in `evals.yml` on dispatch and reports prose vs ensemble deltas to
   Tracer.
5. The decision recorded in BRD §6.1 cites the eval run.

## Docs

BRD §5.6 R6.2 satisfied; §6.1 updated with measured RPD and the eval result; feature set §4 rows
updated.
