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
  - **Token note** (wave 2.5, review Could 8): the block is sent in **every** reduce batch
    (`_reduce_batch(..., single_source)`), so a one-window source with more than 10 candidates
    pays for the window ⌈N/10⌉ times. That is fine for cel (one batch); a long single-window
    source with a large cast is the case to watch.
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
to it (case-insensitive) is dropped and its value kept: `{"voice": "dry"}` under `Voice` renders
`Voice: dry`, not `Voice: voice: dry`. `render_character` and `context._bible_lines` pass their
labels. Without a label the output is unchanged.

Wave 2.5 (review Could 3) removed `_strip_label`, which also stripped a leading `label:` from
string values and list items: the root cause (the eval writing `{"voice": "voice: …"}`) is fixed
in the eval pipeline, so the dict-key drop alone keeps P9.

## 26f — Canonical names, voice samples and movement (wave 2.5)

- **A title is not the name** (eval finding 4). `_fold_group`'s rank puts first a form the source
  does **not** mostly use after a determiner (`the|a|an|this|that`): `_used_as_title(form, text)`
  is one regex per form, true when more than half its occurrences follow one. On cel, "the Chief
  Executive Lich" (2 of 3 in the synopsis) ranks below "Edric Thane", which becomes the name, with
  the title as an alias. "Count Vasska" keeps its honorific (never after a determiner), and a
  form that never occurs in the text is not a title. `merge_characters` memoises the check per
surface for its source, and a one-form group is never checked (one scan per distinct surface).
- **Voice samples carry voice** (eval finding 5). A dialogue chunk needs at least
  `SPEECH_MIN_WORDS` (3) words (`character_extractor._paragraph_indexer`, the one place
  fragments enter; landed with the wave-1 fixes): "Zara," and "So!" are not indexed. On cel the
  shortest principal sample is 3 words and Milo's long lines remain.
- **"How they move" keeps named places** (wave-1 review S2). `context._centres` decides whether a
  narration paragraph centres a character: when its first sentence opens with them, or with
  another cast member, that subject decides; otherwise it is theirs unless it names another cast
  member. The generic capitalised-word loop is gone, so "Milo walked the Marrow District at dusk"
  is Milo's. `build_agent_context` (ensemble) takes the scene's `cast` too; without one, only the
  lead subject can claim a passage.
- **Wave-3 note:** a "How they move" sample can be a one-off plot event ("Milo locked the ledger
  in his desk drawer that night…"), which invites the writer to repeat it. No rule separates a
  mannerism from an event without a heuristic, so it is left for wave 3.

## Tests

- `tests/unit/test_extraction_bible.py`: cel mention order and the exact count; shared surname;
  word and honorific tokens; pronouns/lowercase; evidence as list and string (and a pre-SPEC-26
  checkpoint string) merge; one line per quote; cap spread across windows; the cel reduce prompt
  has ≥ 3 evidence/quote lines per principal, own words only, and a `<source>` block; no
  `<source>` for a multi-window source; quote
  picking; an ambiguous speaker's line is nobody's; the P10 render format.
- `tests/unit/test_prompt_hygiene.py`: `_render_json_field` label dropping (dict key, string and
  list prefixes, case-insensitive, unchanged without a label); `build_cast_context` has no bible
  lines, `bible=True` restores them; `build_agent_context` keeps Description/Goals/Voice; a full
  `write_beat_prose` prompt with `canon.full_block()` has no bible lines under `CHARACTERS IN THIS BEAT`,
  each Description and Goals exactly once, and no `voice: voice:`.
- `tests/unit/test_extraction_scale.py` (26f): on the cel synopsis, Edric Thane is the name and
  "Chief Executive Lich" an alias; "Count Vasska" keeps its title; each surface is scanned once.
- `tests/unit/test_dialogue_attribution.py` (26f): `_centres` keeps "Milo walked the Marrow
  District at dusk" for Milo, refuses "Zara crossed the floor" for him, and follows the lead
  subject of a passage that later names another; `build_agent_context` filters by its cast. The
  3-word minimum is `test_a_voice_sample_is_at_least_three_words`.
- Wave 2.5 deleted `test_cel_principals_get_a_bible` (review S10): it asserted fields its own
  echo fake filled from the prompt, so it could not fail independently of
  `test_cel_reduce_has_depth_and_source`.

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
- Wave 2.5, `docs/BRD.md` R4.4, append: "A form the source mostly uses after 'the' is a title,
  not the name; voice samples are at least three words (SPEC-26 26f)."
- Wave 2.5, `docs/feature-set.md`, extraction review row: append "a title ('the Chief Executive
  Lich') becomes an alias of the real name".

**Wave 12 (W12-4, W9-6).** `_used_as_title` strips the form's own leading determiner (the/a/an/this/that) before its
majority test, so "the Chief Executive Lich" ranks below "Edric Thane". (F) `test_w11_replay_heads_edric_thane`: W11's
archived answers replayed give Edric Thane, the title an alias, F1 1.0 `dev (relay)` (was 0.741); (F) the determiner row.
One-off remerge sweep (uncommitted): 28 archives with `extraction_map` answers, 53 runs; only W8 and W11 change head.
