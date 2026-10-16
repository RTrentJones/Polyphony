# SPEC-51 — JSON-only prompts ask the provider for JSON

**Closes**: W6-6 (a) · **Size**: S · **Depends on**: SPEC-28 (prompt contract). W6-6 (b), the `extraction_map` cap, was dropped (Q11).

## Design
- **One constant.** `app/llm/json_utils.JSON_OBJECT = {"type": "json_object"}` sits beside the parsers that read the answer. Every JSON-only call passes it as `response_format`; there is no provider flag, helper or fallback path (B-1). All five provider rows are OpenAI-compatible, and extraction has sent JSON mode on all of them in production since hotfix #41. A future provider without JSON mode adds the field then.
- **Who sends it.**
  - Extraction (`extraction_map`, `extraction_reduce`, `extraction_synopsis`, one `_json_call`): day-0, byte-identical to the literal it replaced.
  - Outline (skeleton, chapters, beats) and `plan_beats` (E4): an array answer is asked for as `{"items": [...]}`, which `extract_json_array` unwraps by the `items` key, whatever other keys sit beside it (review S-2); a lone other wrapper key is still unwrapped.
  - Single-shot `outline` (`{"items": [...]}` too) and the ensemble's `ensemble:proposal` / `ensemble:review` objects (review S-1). The dead `CharacterExtractor.extract_characters` (no caller) is deleted with its JSON prompt.
  - Continuity (`check_chunk`): both attempts (`for attempt in range(2)`). The retry stays a retry, not a different mode; the warning's `attempt` field still tells the two calls apart.
- **The check.** `prompt_contract.json_mode_where_json` counts each archived call whose purpose is in the literal `JSON_PURPOSES` (the eleven above; a run without single-shot or ensemble mode has n = 0 for those) and passes it when its recorded `response_format` equals `JSON_OBJECT`. It reads a recorded request property, not a product rule. It may not be empty: every six-step run makes JSON calls. The round trip records `response_format` for every call, so the check runs on the app's own requests.

## Baselines (offline regrade, 22 checks)
| Archive | `json_mode_where_json` | failing purposes |
|---|---|---|
| W4b2 | 3 / 11 | outline:skeleton, outline:chapters, outline:beats, plan_beats |
| W5c, W5d | 6 / 15 | the four above + continuity (first attempt) |
| W5bd, W5bc | 3 / 12 | the four outline / plan_beats purposes |

Extraction passes in every archive. Target on the wave-6 runs: **1.0**.

**Risk.** The relay never fences JSON, so JSON mode on a real vendor is only measured by the live run (O-3); the `{"items": …}` shape of the array prompts is the new part.

**Tests**: `test_prompt_contract.py` (W5d-shaped records: outline, plan_beats and continuity fail, extraction passes, prose not counted; the failing-fixture reason), `test_continuity_v2.py` (JSON mode on both attempts), `test_extraction_scale.py` (day-0, byte-identical), the round trip (n > 0).
