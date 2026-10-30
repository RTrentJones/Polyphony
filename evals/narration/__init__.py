"""SPEC-96 (wave 21): the narration and character-setup measures.

- `contract` (M1, no LLM): the narration contract, person, tense and quote
  style of a chapter's narration against the book's own (its source excerpts);
  its validation on source and rule-damaged chapters.
- `lint` (M4, no LLM): the outline lint, beats with no character, POV outside
  the cast, names the cast does not know.
- `judges` (M2, M3): the opt-in `narration_judges` step (narrator forced
  choice, speaker attribution on generated chapters) and `narration_validation`
  (M3's ceiling on source scenes, M2 re-checked on source chapters).
- `analyze`: the pre-registered paired statistic per contrast, Holm across the
  three primary tests, and a power helper.
"""
