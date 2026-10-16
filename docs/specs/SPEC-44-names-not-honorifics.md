# SPEC-44 — Name warnings flag inventions, not honorifics, weekdays or split phrases

**Closes**: W5-5 · **Size**: S · **Depends on**: SPEC-21/33 (`fidelity.unknown_proper_nouns`), `app.parsing.names`.

One rule change in `fidelity.py`, feeding the outline/revise warnings and `names_outside_scene`.

- **Honorifics.** An abbreviated honorific (Mr., Dr., …; `names.ABBREVIATED_HONORIFICS`) with its
  period joins the capitalised run after it: "Mr. Quince" is one run; a whole-word title ending a
  sentence ("the Count.") never joins the next one. A run made only of `HONORIFICS` and `NON_NAME_TOKENS` is
  never reported, and the known check ignores honorific tokens ("Mr. Voss" is known when "Voss"
  is). One list of non-names serves extraction and the warning.
- **Calendar words** also match as plurals ("Fridays"); a possessive ("Monday's") already ended
  the run at the apostrophe.
- **Joined names.** Two reported runs separated by exactly " and " or " of " are one report
  ("Central Escrow and Ledgers", "Department of Records"). A run beside a known name is not
  joined ("Prava and Bartholomew Quince" reports "Bartholomew Quince").
  Policy unchanged: invented institutions still warn (outline "Facilities").

## Measured

Unit table (`test_fidelity_gate.py::NAME_RULE`, canon never writes "Mr"): five rows change from
`['Mr']`, `['Mr']`, `['Fridays']`, `['Central Escrow', 'Ledgers']`, `['Department', 'Records']`
to `[]`, `['Mr. Quince']`, `[]`, `['Central Escrow and Ledgers']`, `['Department of Records']`;
the NB-1 rows and "Milo and Zara" are unchanged. Two rows pin the period rule: "She bowed to the
Count. The Marrow Exchange was shut." reports `['Marrow Exchange']` and "He thanked the Lord. Then
Quince left." `['Quince']` (both `[]` when every honorific joined).

Archived regrade (manual, `pipeline._unknown_canon` over every beat_prose answer; tests never read
the archives): day-0 gives `['Fridays', 'Mr']` on W4 and `['Central Escrow', 'Ledgers']` on W4b2;
after this change `[]` and `['Central Escrow and Ledgers']`, so prose `unknown_canon` over W4 and
W4b2 is exactly `["Central Escrow and Ledgers"]` (the §6.2 gate).
