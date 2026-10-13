# SPEC-10 — Outline → chapter → scene handoff

**Closes**: F9 (and the rest of F10's UX) · **Size**: M · **Depends on**: SPEC-01

## Why

The staged outline already knows the things an author most needs: its restated premise (R1.3
exists to surface a misreading at stage one) and, per chapter, the POV, the characters present and
the act. All of it is discarded. `premise_restated` is never stored or shown;
`validate_outline_nodes` keeps only `title`, `summary` and `children`; promote creates a chapter
with neither cast nor POV, is not idempotent, and loses the link to its node. The author then
retypes names into the scene form.

## Outcome

The author sees and confirms the premise the model understood before chapters are built on it.
Outline nodes carry structured cast, POV and act. Promoting a node is idempotent and carries those
fields to the chapter, and generating a scene in that chapter pre-fills cast, POV and description
from it.

## Scope

**In**: staged outline stages and storage, outline node schema and validator, promote, `Chapter`
fields, the outline panel, the generate-scene modal defaults.
**Out**: automatic scene generation for a whole chapter (a natural follow-up once this lands).

> **Follow-up landed.** `POST /books/chapters/{id}/scenes/generate-planned` (and "Generate all
> planned scenes" on a chapter with planned beats) writes one scene per planned beat with the
> chapter's cast and POV. It goes through the scene preflight (for the whole batch), the "llm"
> rate class and a per-chapter dedupe key, and runs as a chain of jobs (`app/jobs/scene_batch.py`):
> each completed scene enqueues the next, so every scene starts from the one before it and a failed
> or cancelled scene stops the batch. Chapter versions now also carry `pov_character_id`,
> `character_ids` and `plan_node_id`; restore re-applies the cast against the book's live cast, and
> recreating a purged chapter relinks its node only while no live chapter holds it.

## Design

1. **Premise checkpoint.** Store the skeleton on the plan (`BookPlan.premise JSON`:
   `premise_restated`, `central_conflict`, `acts`). Add a plan option `confirm_premise: bool`
   (default true on the free tier, since it saves the most quota when the premise is wrong): the
   job pauses in a new `awaiting_confirmation` state after S1, the outline panel shows the restated
   premise and central conflict with "Looks right" and "Edit and continue" (the edit feeds S2 as
   the authoritative premise), and confirmation resumes the job via its checkpoint (SPEC-08).
2. **Node schema.** `validate_outline_nodes` accepts optional `pov: str`, `character_ids:
   list[UUID]` (validated against the book's cast; names resolved through aliases),
   `act_index: int` and `threads: list[str]` on top-level nodes. The beat stage is asked to echo
   them, and the handler merges S2's structured fields onto S3's nodes by position as a fallback.
   The fidelity audit reads `character_ids` and `pov` as strict structural cast (R1.5's
   "structural cast fields are strict").
3. **Stable node IDs.** Each node gets a `node_id` (UUID) assigned at validation. Versions keep it.
4. **Promote.** `Chapter` gains `plan_node_id`, `pov_character_id`, `character_ids JSON`.
   Promote by `node_id` (keep `node_index` as a fallback), and when a chapter with that
   `plan_node_id` exists, return it with 200 rather than creating a duplicate. Add "Promote all".
5. **Scene defaults.** The generate-scene modal pre-selects the chapter's characters and POV and
   offers each planned beat (the node's children) as a one-click scene description.

## Acceptance

1. A staged run with `confirm_premise=true` stops after one LLM call with the plan in
   `awaiting_confirmation` and the premise stored; confirming with an edited premise resumes and
   the edited text appears in the chapters-stage prompt. *(unit, stubbed client)*
2. A stored outline round-trips `pov`, `character_ids` and `act_index` through
   `validate_outline_nodes` and a restore. *(unit)*
3. Promoting the same node twice yields one chapter. *(unit)*
4. A chapter promoted from a node with two characters opens the modal with those two pre-selected.
   *(frontend: covered by a component test once SPEC-15's test harness exists; until then, a manual
   check recorded in the PR)*

## Docs

BRD R1.3 gains "and is shown to the author for confirmation before the chapters stage"; feature set
§3 adds premise confirmation and structured nodes.
