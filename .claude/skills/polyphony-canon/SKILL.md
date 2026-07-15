---
name: polyphony-canon
description: The contract for Polyphony's Canon model and its generation behaviour — book-as-root, untruncated first-party prose, versioned canon, cast fidelity, free-first tiering. Use when changing anything that feeds an LLM (outline, prose, ensemble, continuity, extraction), anything touching Character/Source/canon scoping, sanitization, versioning, or the evals that gate them. Read the BRD before changing behaviour, and update it in the same PR.
---

# polyphony-canon — the generation contract

Polyphony's product behaviour is specified in [`docs/BRD.md`](../../../docs/BRD.md), with the
decisions in [`docs/ADR-002-book-as-root.md`](../../../docs/ADR-002-book-as-root.md) and the
catalogue in [`docs/feature-set.md`](../../../docs/feature-set.md). **The BRD is the source of
truth: change it first, then the code, in the same PR.** A behaviour change that isn't in the BRD
isn't a decision — it's a drift.

## Why this skill exists

A 20,001-char synopsis produced an outline starring an invented protagonist ("Elara") and read
the in-world "the CEL" (Chief Executive Lich) as a company name. The model had received **2,000
of 20,001 chars and an empty character bible**. The most recent commit before the incident had
*tuned the prompt to beg for fidelity* — against an eval that scores structure, not fidelity, and
a corpus 4× too small to trigger the truncation at all.

**The lesson: when generation is unfaithful, suspect the input pipeline before the prompt.**
Prompt wording cannot recover text the model never received.

## The five invariants

Break one and you reintroduce the incident.

1. **The whole canon reaches the model.** No truncation, no HTML escaping, no pattern filtering
   of first-party prose. If content exceeds a bound, **raise** — never silently cut. A silent cap
   is what kept this bug invisible for its entire life. (BRD R1.1, R2.1–R2.2)
2. **Book is the root.** Every canon concept is reachable only through a book. No NULL-scoped
   entity, no user-level bible. `book_id` is `NOT NULL`. (BRD R3.x, ADR-002 §1)
3. **Nothing the author wrote is destroyed.** Every mutation appends a version first;
   regeneration appends; restore is forward-only. (BRD R4.x, ADR-002 §5)
4. **Fidelity is gated deterministically.** Principal-cast recall is a hard gate — exact match,
   free, no false positives. Unknown proper nouns only warn (a good outline may invent an
   innkeeper). Never hard-fail an expensive job on a heuristic. (BRD R1.4–R1.6)
5. **Free tier never loses work.** Quota exhaustion pauses and resumes; it does not fail.
   (BRD R7.2)

## Before you change generation behaviour

- **Read the BRD section** covering what you're touching. Update it in the same PR if behaviour changes.
- **Trace what actually reaches the model.** Print the assembled prompt. Do not assume a field is included because the code names it — `plans.py` queried `book_id` for months against a column nothing ever wrote, and the bible was silently `""` the whole time.
- **Ask whether the eval can fail.** If the corpus can't trigger your code path, the green is meaningless. The truncation branch never executed in a single eval run.
- **Prefer deterministic checks to judges.** Free, trustworthy, no API spend. The judge is secondary.

## Traps this codebase has already sprung

- **`sanitize_for_llm` is deleted.** Do not reintroduce lexical scrubbing of author prose. Injection defence is **structural** — fence the content and tell the model it's story material. There is no untrusted-content boundary in a single-author product; if sharing ever ships, the fence is what scales, not a regex that eats em-dashes. (ADR-002 §4)
- **`characters.book_id` must be `CASCADE`, not `SET NULL`.** With `NOT NULL`, `SET NULL` violates the constraint and 500s book deletion.
- **Source→character cascade has two halves.** The DB FK *and* the ORM `cascade="all, delete-orphan"`. Change both or neither, or SQLAlchemy still deletes the canon in Python. Deleting a source file must never delete the cast.
- **Migration baseline drifts.** `0001` calls `Base.metadata.create_all`, so a fresh DB gets the current ORM shape and additive migrations run against already-correct state. This already bit `0006`. Guard every statement, or squash.
- **Character agents must not receive the whole canon.** It costs ~2.5× and is wrong in fiction — a character does not know the plot. Full canon is for the narrator and the editor only.
- **Versions are unpruned**, so the ensemble must **never** snapshot per agent turn — only the final scene.
- **`_generate_action` derives action from dialogue** post-hoc. That dependency is backwards: action is first-class in the request, not a garnish on the reply. (BRD R6.1)

## Verifying a generation change

The acceptance test for this whole area — load the real storyboard from `docs/examples`, generate
a 12-chapter outline, and assert it stars **Milo Voss** and **Zara Okafor** against **Edric
Thane** in **Aeon Holdings**, with no invented protagonist and "the CEL" read as the **Chief
Executive Lich**.

```bash
make test          # the CI ship gate
make lint          # black --check + ruff
make dev           # local stack, then drive the real flow
evals/.run-eval    # cast_fidelity >= 0.9 on the `cel` corpus gates CI
```

Shipping follows the wrapper repo's `deploy-verify-promote` skill: pytest → build → GHCR →
dispatch → SHA-gated `/__version` verify.
