# SPEC-47 — A redo changes what was asked, and says what it protects

**Closes**: W6-2 (a)(b)(c) · **Size**: S/M · **Depends on**: SPEC-41, wave-6 day-0 (`w5_archives.json`).

## Design
- **Direction line (D7).** An instruction is a *direction* when, after one regex removes its leading `Redo beat N[.:]` (any case), it is non-empty. A redo with a direction prints `REDO_CHANGE`: "Change what the instruction asks for. Keep every other sentence of the beat as written, unless it must change to fit the change." A terse one ("Redo beat 2.") keeps `REDO_DIRECTION`. A range or whole-scene revise prints neither (SPEC-41).
- **Number words with their noun (D5).** A number word is a callback only as "number + the next word of its sentence" ("nine days", "two fingers"), and that pair must recur in `rest`. A bare "two" is never one. Digits, hyphenated forms, capitals and dash-led fragments are unchanged.
- **Callbacks with context (D5).** `KEEP_LINE` renders each term as `term ("context")`. `callback_context(term, rest)` is one lookup over the same `callbacks()` output: the term's first use in `rest`, as its sentence's words from up to three before it to up to three after it, at most 8 words, outer punctuation trimmed. A sentence ends at `. ! ? …` (with closing quotes) before whitespace, or at a newline. No scene label.
- **Quoting.** `DROPPED_CALLBACK` quotes each term with `'…'`, never Python `repr` ("This version drops 'Appendix F's', …").
- **The beat by its number (wave-5 Could 5).** The job reads `(beat_number, description)` and marks the position of `beat_number == beat_index`, the beat the API located, so a gap in the numbers cannot mark the wrong beat.
- **`untouched_share`** (W6-2 (d)) is E1's report; RA stays with the judge (D6).

## Archived lists (`tests/fixtures/w5_archives.json`, `callbacks`)
| Redo | `keep` (term → context) |
|---|---|
| W4 `0031` | Forty-one → "had looked at forty-one years of him"; nine days → "reporting cycle from nine days down to six" |
| W5 `0031` | none (was "two": "two fingers" does not recur) |
| W5bd `0030` | 4,117 → "from 3,902 to 4,117 without passing through" |
| W5bc `0030` | —could → "that, you know, could have" |
| W4b2 `0026` | —list → "a short bulleted list had been left"; B-flat → "that hummed in B-flat, and it had" |

## Notes
- **W4 becomes "Forty-one, nine days" (Q7).** "nine-to-six" never occurs in the archive; the cycle is "nine days … down to six", and a bare "six" closes its sentence, so it is no longer a callback.
- The sentence split is a heuristic: "Mr. Voss" ends a sentence for the context lookup only. The context is shown to the model, never matched against its answer.
- `dropped_callbacks` still compares terms only, and stays warn-only.
