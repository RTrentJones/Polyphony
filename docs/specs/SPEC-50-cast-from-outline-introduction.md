# SPEC-50 — "Add to cast" stores an introduction

**Closes**: W6-5 (a) · **Size**: S · **Depends on**: wave-6 day-0
(`SuggestedCharacter.introduction` in `types.ts`), SPEC-40 (`secondaries.introduction`).

## Behaviour

`suggested_characters` returns `introduction` beside `synopsis_sentence`: SPEC-40's cut of the
same naming sentence (through its first appositive, less a trailing "and"), "" when nothing names
them. The Outline panel's "Add <name> to cast" creates the minor character with
`description: introduction`; the whole `synopsis_sentence` stays the chip's tooltip.

For cel, Prava's description becomes "There is Prava, the intake caseworker who onboarded Milo
with such practiced warmth", without the act-3 "private ledger" clause that the canon block then
carried into every chapter-1 prompt. A canon-entry fallback sentence with no comma is kept whole.

## Out of scope

Rewriting existing characters' descriptions; LLM-written summaries. The eval's minor-cast commit
(W6-5 (b)) is harness hygiene in SPEC-52.
