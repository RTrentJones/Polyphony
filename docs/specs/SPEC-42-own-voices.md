# SPEC-42 — Each speaker sounds like only themselves

**Closes**: W5-3 (a), (c) · **Deferred**: W5-3 (b), the contrast line (`NO_SAMPLES_NOTE` stays; a
wave-6 item if R2 on a secondary speaker is below 0.85 in both wave-5 runs) · **Size**: S.

## Owned phrases (`app/characters/context.py`)

`owned_phrases(character)` returns the quoted spans of two or more words in the character's canon
voice field, as the author wrote them; nothing is inferred. Edric's field gives `["Mr. Voss"]` (not
the one-word 'one'). `build_cast_context` appends, once after the beat's character blocks, one line
per book-cast member who is not in the beat and owns a phrase: `Only Edric Thane says: 'Mr. Voss'`.
Beat prose and revise get it through that function; a beat that casts the owner does not carry it.
The grader `borrowed_phrase` (SPEC-45) reads gold, not this rule.

## The Edric probe diagnostic (W5-3 (c))

Edric has **3** dialogue chunks of ≥ 3 words in the attribution store (52, 22, 35 words), and
`character_samples` already fetches 6. The probe's raw scores (the app's MiniLM embedder) are
0.2382, 0.1896 and 0.1707; `retrieve_similar` keeps only rows ≥ `RAG_SCORE_THRESHOLD` (0.2) unless
none passes, so the probe shows one sample. **The cause is the threshold**, not the corpus or `k`,
so it is recorded and deferred. `retrieve_similar` already takes `score_threshold` (`store.py:140`):
a wave-6 fix is a call-site argument for the probe, not a store redesign.
