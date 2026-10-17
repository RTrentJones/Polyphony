# SPEC-57 — Canon text hygiene: contractions are not names; introductions start at the name

**Closes**: W7-5 (Q11, Q12, "Don") · **Size**: S · **Depends on**: SPEC-40, -42, -44.

## Design
- **Contractions** (D7, Q4). In `fidelity._candidates`, beside the label rule, a run followed
  directly by `'t`, `'ll`, `'re`, `'ve` or `'d` (straight or curly) is not a name.
  `unknown_proper_nouns` and the `proper_nouns` denominator skip "Don" in "'Don't explain.'". `'s`
  is never skipped, so "Gary's desk" still names Gary. Residual: "Quince'll" is not reported.
- **Introductions** (D8). `introduction()` drops "There is / was / are" only when the name follows
  it directly: "There is Old Fen, who…" becomes "Old Fen, who has been in Records so long that he has
  begun to file himself".
  "There is a clerk, Prava, who…" still becomes "Prava, who…" (the unchanged comma rule); "There
  was once a clerk called Prava, who…" keeps its words. The find stays case-insensitive.
- **Owned-phrase note** (Q12): a guard row only. The note appears exactly when an absent member owns
  a phrase, and always directly over a filled `<owned>` block.

**Effect**: the secondary block and the minor descriptions change for Prava and Old Fen. A paused
job with an Also-named secondary restarts once, which goes in the PR note.
**Tests**: `test_fidelity_gate.py`, `test_secondaries.py`, `test_evals_steps.py`.
