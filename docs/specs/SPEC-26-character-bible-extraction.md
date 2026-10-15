# SPEC-26 — Extraction writes a real character bible; beat prompts stop repeating it

**Closes**: W2-5 (F1, F6 residual), W2-12 (F4 reframed, F7) · **Size**: M · **Depends on**:
SPEC-09 (map-reduce extraction), SPEC-17 (dialogue attribution) · **Wave**: 2 (package E4)

Owns the prompt blocks **P8, P9, P10** of the wave-2 contract (`tpm-plan-wave2.md` §1.3), which
E6's `prompt_contract` grader checks (`no_duplicate_bible`, `no_doubled_voice_label`,
`reduce_evidence_depth`).

## 26a — Mention counts (F1)

`app/parsing/canon_extractor.py`: `merge_characters` → `_count_group_mentions`.

- A candidate's mentions count every form it is known by (name + aliases, as before) **plus**
  each capitalised given name or surname of those forms that **no other candidate shares**. On
  cel, "Milo" counts for Milo Voss though the map never listed it as an alias; with both
  Jonathan and Mina Harker present, a bare "Harker" counts for neither.
- Honorifics (`Mr.`, `Count`, …) and word-tokens (`The`, `Old`, `Van`, …) are never counted alone:
  "The end" is not The Lich, and "Old habits" is not Old Fen ("Fen" is).
- Matching stays case-sensitive on the surface form, longest form first, so "Milo Voss" is one
  mention, not two, and "milo" is not Milo. **Pronouns are never counted**; the review label now
  reads **"name mentions"** (`frontend/app/sources/review/page.tsx`, with a tooltip saying so).
- The candidate's own window appearances remain the floor.

cel: Milo 13 (≥ the 10 literal "Milo"), Zara 8, Edric 4 → Milo ≥ Zara ≥ Edric.

## 26b — Evidence depth (F1)

- **Map** asks for `"evidence": [...]`, up to 3 short quotes per character per window
  (`_MAX_MAP_EVIDENCE`), each one line (whitespace collapsed) and ≤ 600 chars. The parser accepts
  a **string or a list** (`_evidence_list`): the eval stub still sends a string, and a checkpoint
  written before this change holds one string per window, so a resumed job keeps working.
- The map answer budget is `est_tokens // 4` (was `// 6`), still capped at 8,192, to fit the
  extra quotes.
- **Merge** keeps up to `_MAX_EVIDENCE = 8` distinct quotes, spread across windows (every
  window's first quote before any window's second), read in source order.

## 26c — Reduce input (P10)

`_reduce_prompt(batch, total_windows, source=None)`:

- Per `### Name` candidate: `Evidence:` then `- (part N) …` (≤ 8), then, when the candidate has
  attributed dialogue, `Quoted lines (their own words):` then up to 6 lines `- "…"`.
- Quoted lines come from the SPEC-17 attributor (`attribute_speech` once over the source, then
  `spoken_lines` per candidate with the whole merged cast for ambiguity): only words a tag or
  action beat credits to them; a speaker that could be two candidates is nobody's. Up to 6
  distinct lines, those of ≥ 4 words first, kept in source order (`_pick_quotes`).
- A **single-window source** adds one `as_quoted_block(window_text, "source")` block before the
  candidates. Multi-window sources do not (the window is the map's job).
- The instruction now asks the model to read what each one does and says (description = who
  they are, goals = what they want or are trying to do, voice = how their quoted lines sound) and
  to leave a field empty only when nothing bears on it. The no-invention rule stays.

cel (one window): each principal's section has ≥ 3 evidence/quote lines (Milo 3 + 5, Zara 3 + 5,
Edric 3 + 2), plus the `<source>` block.

## 26d — No duplicate bible in beat prompts (P8, F4 reframed)

`app/characters/context.py`:

- `build_character_context(..., *, bible: bool = False)`. With `bible=False` the block is
  `### Name`, `Voice samples …` and `How they move:` only — no `Role:`, `Description:`,
  `Goals:`, `Arc:` or `Voice:` lines. The bible lines moved to `_bible_lines`.
- `build_cast_context(..., *, bible: bool = False)` passes it through. Its callers — beat prose
  (`app/orchestration/prose.py`) and revise (`app/jobs/revise.py`) — both send
  `canon.full_block()`, which still carries the **whole** bible (BRD R1.1 is unchanged: the
  whole canon on every call). Only the second copy is gone.
- `build_agent_context` (ensemble) passes `bible=True`: the ensemble agent prompt has no canon
  block.

## 26e — No doubled labels (P9, F7)

`app/planning/canon.py`: `_render_json_field(value, label=None)`. With a label, a dict key equal
to it (case-insensitive) is dropped and its value kept, and a leading `label:` on a string value
(or list item, or that key's value) is stripped: `{"voice": "dry"}` under `Voice` renders
`Voice: dry`, not `Voice: voice: dry`. `render_character` and `context._bible_lines` pass their
labels. Without a label the output is unchanged.

## Tests

- `tests/unit/test_extraction_bible.py`: cel mention order and the exact count; shared surname;
  word and honorific tokens; pronouns/lowercase; evidence as list and string (and a pre-SPEC-26
  checkpoint string) merge; one line per quote; cap spread across windows; the cel reduce prompt
  has ≥ 3 evidence/quote lines per principal, own words only, and a `<source>` block; a fake LLM
  that writes fields only from the material it was given yields non-empty description, goals and
  voice_characteristics for the three principals; no `<source>` for a multi-window source; quote
  picking; an ambiguous speaker's line is nobody's; the P10 render format.
- `tests/unit/test_prompt_hygiene.py`: `_render_json_field` label dropping (dict key, string and
  list prefixes, case-insensitive, unchanged without a label); `build_cast_context` has no bible
  lines, `bible=True` restores them; `build_agent_context` keeps Description/Goals/Voice; a full
  `write_beat_prose` prompt with `canon.full_block()` has no bible lines under `CHARACTERS IN THIS BEAT`,
  each Description and Goals exactly once, and no `voice: voice:`.
- Existing `test_extraction*.py` and `test_dialogue_attribution.py` pass unchanged.

## Docs (BRD / feature-set delta for the orchestrator)

- `docs/BRD.md` R4.4 (extraction), append: "Reduce writes each character from up to 8 evidence
  quotes, their own attributed dialogue (up to 6 lines) and, for a one-window source, the source
  itself. Mention counts include an unambiguous given name or surname; pronouns are not counted,
  and the review says 'name mentions' (SPEC-26)."
- `docs/BRD.md` R1.1, append: "The beat-cast blocks carry voice samples and movement only; the
  bible is sent once, in the canon block (SPEC-26)."
- `docs/feature-set.md`, extraction review row: notes → "ranked by parts seen in, then name
  mentions (given name / surname counted when unambiguous; pronouns not counted) (SPEC-26)".
- `docs/feature-set.md`, scene generation row: append "the bible is sent once per beat prompt
  (canon block); per-character blocks carry voice samples and movement (SPEC-26)".
