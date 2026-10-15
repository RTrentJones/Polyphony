# SPEC-17 — Voice samples are the character's own words

**Closes**: backlog B2 (P0, writing quality) · **Size**: M · **Depends on**: SPEC-09 (narrator
sections), SPEC-11 (voice workbench, grounding record), SPEC-14 (split voice/physicality)

## Why

Voice samples are the product's premise, and they were wrong. Milo Voss's Voice panel listed
Zara's line `"Don't thank her,"` and the caseworker's `"You've been retained,"`. The cause was
`app/parsing/character_extractor.py`'s catch-all pattern `rf'{name}\s+[^"]*"([^"]+)"'`: `[^"]*`
ran across sentences and paragraphs to the next quotation, so any quote after a character's name
became that character's dialogue. The prose prompt then printed these under "Voice samples (match
this voice — cadence, diction, syntax)", next to narration that was about someone else ("Milo Voss
read the appendices. Zara Okafor watched him read them." was in Zara's samples).

## Outcome

A character's dialogue chunks contain only words a tag in the source credits to them. The prose
prompt shows those words under **"Voice samples (their own words — match cadence, diction,
syntax):"** and the character's action/description/thought separately under **"How they move:"**.
Narration that centres another character is left out. Existing books get the fix with
`python -m app.cli reindex-voice --book <id>`.

## Design

### 1. Quote-span attribution (`app/parsing/character_extractor.py`)

`attribute_speech(text)` returns, per paragraph (`text.split("\n\n")` indices), each quoted span
and the speaker a tag credits it to (the tag's subject as written, such as "Zara Okafor" or "the
caseworker"), or `None`. `speaker_matches(speaker, name, aliases, cast)` resolves a speaker to a
character in **one direction only**: every word of the speaker, minus honorifics and determiners,
must appear in one of the character's name forms (the name or an alias). So "Zara" and
"Ms. Okafor" resolve to Zara Okafor, but "the auditor to Milo" does not resolve to Milo, and
"Harry Potter" does not resolve to a character named only "Harry" (give them the alias). A speaker
that is only an honorific ("Count") is compared on its raw words. With a cast, a speaker within
two members' names (for example "Ward" when there are two Wards) is credited to nobody. The
two-way `app.parsing.names.match` is unchanged and still used where both sides are names (narrator
headings, extraction merge, `_centres`).

Rules, in precedence order, for each span:

1. **Tag after the span** opens the narration that follows it: `"…," said X` or `"…," X said`
   (an adverb is allowed). `X said nothing` is not a tag. The speaker's noun phrase stops at a
   preposition, so `"…," said the auditor to Milo` is the auditor's and `"…," said Corin to the
   porter` is Corin's. `X told Y` / `X asked Y` are X's.
2. **Tag before the span** closes the narration that precedes it: `X said, "…"`, `said X, "…"`,
   or `X: "…"`.
3. **Action beat.** `X shrugged. "…"` counts only when X is the proper-name subject of the
   sentence immediately before the quote and that sentence contains no other capitalised word.
   A beat after the quote (`"…" X shrugged.`) is not a tag.
4. **Pronoun tags credit nobody.** `he/she/they/I/we/you said` is kept as the (lowercased)
   pronoun: it matches no cast member, and because it is a tag it blocks rule 3's beat. In a
   mixed-gender exchange the beat's subject is as often the listener (`Milo looked at her. "Sit,"
   she said.` is not Milo's). In a narrator's own section, `said I` is still the narrator's own
   speech for indexing (§2). Leading openers are dropped ("Then Zara" → "Zara"), and a lone
   opener or determiner ("For", "The") is not a subject.
5. **One speaker per paragraph.** A span with no tag and no beat belongs to the paragraph's one
   named speaker (`"Hi," said Zara. "Come in."`, or a split quote). When the paragraph names two
   speakers, or none, it stays uncredited.
6. **Across paragraphs**, only two cases are allowed. An untagged paragraph that opens with a
   quote continues the previous paragraph's open (unclosed) quote. A paragraph that is pure
   untagged speech follows an exchange that so far strictly alternates between exactly two named
   speakers. Any narration paragraph, or a paragraph with an unknown, mixed or pronoun-only
   speaker, ends the exchange.

Nothing else crosses a blank line or another quotation. When the rules do not decide, the span is
credited to nobody: fewer samples beat wrong samples.

Smart quotes are normalised. A paragraph with no double quotes falls back to single-quoted
speech, and apostrophes neither open nor close a span.

### 2. What gets indexed (`extract_character_content`, `extract_narration`)

Both go through one per-paragraph rule (`CharacterExtractor._paragraph_indexer`), with one
case-insensitive dedupe across the source:

- A paragraph where the character speaks yields **one `dialogue` chunk per run of their spans**,
  containing their words only. A split quote is one utterance; the narration around the line is
  not their voice. **A voice sample is at least 3 words**: a bare vocative or exclamation
  (`"Zara,"`, `"So!"`) is not indexed, and the paragraph is not indexed as narration either.
- A paragraph that carries anyone else's speech is **not indexed for this character**. That
  covers a span credited to someone else (a pronoun tag included), and an uncredited span of at
  least 3 words; shorter uncredited spans are treated as scare quotes. Without this rule, another
  speaker's words would reach this character's samples (`"You've been retained," the caseworker
  said. Milo Voss nodded slowly.` is not Milo's).
- Any other paragraph that names them is indexed as action, thought or description, as before.
  `_classify_chunk` only ever types narration (dialogue chunks are the character's own lines).
- `extract_narration` (SPEC-09) applies the same rule to its alias and first-person-narration
  paragraphs. This matters in practice: the stub extraction gives the cast aliases ("Milo",
  "Zara"), and the alias pass was how the real browser journey still put the caseworker's line in
  Milo's chunks after the name-gated path had been fixed. In a narrator's own section,
  `"…," said I` counts as the narrator's speech, not someone else's.
- `extract_character_content` and `extract_dialogue_only` accept optional `aliases=` and `cast=`;
  the pipeline passes both (see "Integration").

### 3. The prose prompt (`app/characters/context.py`)

`build_character_context` retrieves the voice block and the movement block separately, through
the shared `_voice_and_movement`. `build_agent_context` (ensemble) uses the same helper.

- **Voice samples (their own words — match cadence, diction, syntax):** dialogue chunks, plus
  first-person narration that SPEC-09 indexed for its narrator (`source_location` `narration_*`).
  A diarist's narration is their own words. The voice block **never falls back** to untyped
  retrieval. For the ensemble this replaces SPEC-14's `<voice>` fallback, which could put
  narration in `<voice>`.
- **How they move:** action, description and thought, up to 3 samples. When fewer than
  `_MIN_TYPED_HITS` (2) typed hits come back, the block falls back to untyped retrieval, minus
  dialogue and anything already shown.
- **Centring.** Name-gated paragraph chunks (`paragraph_*`) reach the movement block only when
  `_centres(text, name, cast, aliases)` holds. That is true when the character is the subject of
  the first sentence. Otherwise it is true only when the passage names no other cast member (by
  full name, alias or a significant name token) and no other proper name after a sentence's first
  word. Manual, dialogue and narration chunks are not filtered. Wave 3: the cast is the scene's
  (`SceneScope.cast`), and the movement filter of SPEC-30 applies to every movement sample (§3a).
- The grounding record (SPEC-11) lists every voice and movement chunk used, deduplicated by id.
  `_retrieve` deduplicates a chunk that a store returns under several types.

### 3a. Wave-3 amendment: one retrieval, one renderer, and the voice probe (W3-5)

- **One retrieval.** `context.character_samples(character, query, k, *, scope)` returns
  `(words, movement)`: up to `k` own-words samples and up to `min(k, 3)` movement samples. It is
  the only voice retrieval. Beat prose and revise (through `build_character_context`), the
  ensemble (`build_agent_context`) and the voice workbench's "Test dialogue" all use it.
- **The speech minimum.** A dialogue sample shows only with 3+ words (`_SPEECH_MIN_WORDS`, the
  same minimum the indexer uses). Retrieval fetches `2k` dialogue hits so that short lines
  ("So!", "Zara,") indexed before that rule, or added by hand, do not take the slots.
- **One renderer.** `context.render_character(name, words, movement)` prints `### name`, then
  `VOICE_SAMPLES_LABEL` with the words, then `MOVEMENT_LABEL` with the movement.
  `build_character_context` composes `character_samples` and `render_character`, and adds the
  bible lines under the heading when asked.
- **Movement.** The label is now "How they move (gesture and habit only; do not reuse these
  events, objects or names):", and a movement sample must name no one outside the scene
  (SPEC-30, "Movement samples"). `_centres` judges a sample against the **scene's** cast
  (`SceneScope.cast`), not the beat's.
- **Name forms.** `_forms` never treats a word that is not a name (`NON_NAME_TOKENS`: "the",
  "old", "van" …) as a name form. `_names_in` matches a one-word form only where the text
  capitalises it, and a multi-word form in any case or spacing. A first sentence that opens with
  "The" opens with no one. Before this change, "the" named "the Lich", so every sample containing
  "the" was dropped as Edric's in Edric's scenes (wave-3 review B-2). The residual risk: a
  sentence-initial "Will" or "Rose" still names a character of that name.
- **The voice probe** (`app/characters/dialogue.generate_dialogue(*, character, prompt,
  context, scope, user_id)`, one caller: `POST /characters/{id}/test-dialogue`):
  - the scope is a scene of one, `SceneScope.of(build_canon(book), [character], [])`;
  - the prompt holds the fenced `render_character` block, the author's context line (left out
    when blank) and the prompt line. There is no `Setting:` line, no emotional state, no other
    characters and no previous dialogue;
  - `_generate_action` gets the same block, so there is one retrieval and two calls;
  - `confidence_score` is the mean similarity to the **full** texts of the own-words samples,
    not to the grounding excerpts and not to movement;
  - `grounding` is `grounding_entries(character, words + movement)`, the same rows a scene
    records. The workbench shows each row's score and `excerpt`, or its `reason` when no sample
    grounded the line;
  - deleted: the second `retrieve_similar` path, `retrieved_examples`, the TTL dialogue cache
    (`CACHE_DIALOGUE`) and the unused `DialogueRequest` / `DialogueResponse` models. The cache
    ignored the context, so a new context returned the old line.
- **Grading.** The dialogue prompt carries `### name` + `VOICE_SAMPLES_LABEL`, so prompt_contract
  `voice_dialogue` (and its `short_samples` count) grades `purpose="dialogue"` too. The round
  trip runs one probe for Milo and asserts `short_samples == 0`.

### 4. Re-indexing existing books (`app/cli.py`)

`python -m app.cli reindex-voice --book <id>` re-runs `pipeline.index_characters_voice` for each
non-trashed source of the book. It covers the characters that source already voiced (from
`voice_chunks.source_id`), was extracted for (`Character.source_id`) or is linked to
(`source_characters`). `index_characters_voice` clears only that source's chunks per character,
so manual samples and other sources' voice are untouched. The command is idempotent. **Existing
books keep their bad chunks until this runs.** The orchestrator runs it on the eval DB.

## Acceptance → tests

| Criterion | Test |
|---|---|
| e2e `SOURCE`: Milo has 0 dialogue chunks, Zara exactly `Don't thank her.`, the caseworker's line is no canon character's | `tests/unit/test_dialogue_attribution.py::TestE2ESource` |
| Never crosses a paragraph or another quotation | `TestAttributionRules::test_never_crosses_*` |
| 20 hand-labelled lines, 3 speakers (inversions, beats, multi-paragraph exchange), precision ≥ 0.95, gates CI | `tests/fixtures/dialogue_attribution.json` + `test_attribution_precision_gate[in-sample]` (precision 1.00, recall 0.89; recall also has a 0.5 floor so the gate cannot pass vacuously) |
| The same gate on 17 **held-out** lines written before the review fixes and never tuned on: `said X to Y`, `X told/asked Y`, pronoun tags after a listener's beat, a vocative, a beat naming a place | `held_out` in the fixture + `test_attribution_precision_gate[held-out]` (precision 1.00, recall 0.64; the pre-fix rules scored precision 0.56 here) |
| `"…," said the auditor to Milo` credits nobody in the cast; a pronoun tag credits nobody; `X shrugged. "…"` still credits X | `TestAttributionRules::test_said_x_to_y_is_xs_line`, `test_speaker_must_lie_within_the_name`, `test_pronoun_tag_credits_nobody`, `test_action_beat_needs_a_lone_subject` |
| A voice sample is at least 3 words (`"Zara,"`, `"So!"` are not indexed) | `TestChunking::test_a_voice_sample_is_at_least_three_words` |
| Only dialogue under "Voice samples"; action under "How they move"; non-centred narration excluded | `TestCharacterContext`, `test_centres` |
| Wave 3: "the" and "chief" are not Edric; Bell, an outside-scene Edric and an unnamed Old Fen are dropped, with no refill (cel cast) | `TestMovementInScene` |
| Wave 3: the probe shows `### name` + own words of 3+ words only, no `Setting:` or "neutral"; scored on full texts; grounding rows with `excerpt` | `tests/unit/test_dialogue_probe.py`, `test_voice_workbench.py::test_test_dialogue_returns_grounding_entries`, `VoicePanel.test.tsx` |
| PG: paste `SOURCE`, index voice, Milo has no dialogue chunk | `tests/integration/test_pg_voice_attribution.py` (+ the B1/B2/vocative source through the real pipeline; `reindex-voice` replaces stale chunks, keeps manual ones, idempotent) |
| Browser: Milo's Voice panel has no `Don't thank her` / `You've been retained` | step for the orchestrator to add to `scripts/e2e_browser.py` (below) |
| Judge R2 ≥ 0.7 | measured in the after-eval (TPM plan §3); not blocking |

## Integration (done)

- **Browser step**: after "the cast is in the canon", the journey opens Milo Voss's Voice panel
  and asserts no sample text contains `Don't thank her` or `You've been retained`.
- **Pipeline**: `index_characters_voice` passes `aliases=` and `cast=`, so aliases count as tags
  and an ambiguous tag ("Ward" with two Wards) is credited to nobody.
- **Quote halves**: one speaker's spans in a paragraph join into one line, and the tag's comma
  closes to a full stop when the next half starts a sentence (`"Don't thank her," she said.
  "Read the appendices first."` → `Don't thank her. Read the appendices first.`). A line that
  ends on the tag's comma ends on a full stop, so Zara's e2e sample is `Don't thank her.`
- Books indexed before this change keep their old chunks until `reindex-voice` runs; a fresh
  eval book needs nothing.

## BRD / feature-set delta (applied by the orchestrator)

- **BRD R5 (voice):** add "Voice samples are the character's own words: dialogue is attributed
  only from a tag adjacent to the quote in the same paragraph (or an unambiguous action beat or
  exchange). Precision is ≥ 0.95 on a hand-labelled fixture, which gates CI. The prose prompt
  separates 'Voice samples (their own words)' from 'How they move'."
- **Feature set §6 (voice):** add a row for the "Quote-span speech attribution" extractor
  (SPEC-17) and a row for the "`reindex-voice --book <id>` admin command". Update the prose-prompt
  row to "voice samples (dialogue + own first-person narration) and movement (action /
  description / thought, centred on the character) as separate blocks".

## Docs delta, wave 3 (applied by the orchestrator)

- **BRD R5 (voice):** "Test dialogue" grounds on the same samples, with the same renderer, as
  scene prose (one retrieval path), and shows the grounding rows it used. Voice samples are 3+
  words.
- **Feature set §6 (voice):** update the "Test dialogue" row: "scene-path samples (own words, 3+
  words, movement filtered to the probe's character); shows its grounding excerpts". Remove the
  dialogue cache from any settings list.

## Known limits

- First-person `"…," I said` lines are not credited to a narrator character. Their narration
  still is, through SPEC-09 sections. Mapping `I` to the section's narrator is a follow-up.
- A diarist's narration paragraph that quotes someone else is no longer indexed for the diarist
  (precision over recall). Dracula-style journals lose some narration samples.
- Every pronoun tag (`"…," she said.`) is credited to nobody, even right after a beat by the
  same person (`Zara shrugged. "Fine," she said.`). This is the main recall loss (in-sample
  recall 0.89, held-out 0.64). Resolving a pronoun safely needs the character's pronouns on
  `Character`, which is a follow-up.
- Speaker matching is one-directional, so a tag that uses MORE of a name than the cast entry
  has ("said Harry Potter" when the character is "Harry") credits nobody unless the fuller name
  is an alias.
- One speaker per paragraph is assumed for untagged spans: `"Hi," said Zara. The door opened.
  "Who?"` credits "Who?" to Zara.
- The beat rule cannot tell a name from a capitalised common noun at the start of a sentence
  ("Dressing-gown"). Such a "speaker" matches no cast member, so it costs nothing.
