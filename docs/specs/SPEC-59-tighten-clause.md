# SPEC-59 — Tighten cuts words, not meaning

**Closes**: W8-1 (a) · **Size**: S · **Depends on**: wave-8 day-0 (`TIGHTEN_CLAUSE`).

## The clause (D1)
`build_revise_prompt` prints `TIGHTEN_CLAUSE` on its own line after the return rule, when the
instruction, with any `Redo beat N` lead removed, contains `cut`, `tighten`, `trim`, `shorten`,
`condense` or `pare` as a whole word, in any case (`_CUT_WORDS`, beside `_REDO_LEAD` in
`revise.py`). It is a direction, not a quota: `tighten_cut` is measured, never instructed (S-1).
The list is the product's rule; no grader reads it (the grader reads `kind: "tighten"`).

| Instruction | Clause | Redo line |
|---|---|---|
| "Tighten this paragraph: cut every redundant word." (all 8 archived asks) | yes | — |
| "Trim the ending", "Cut the adverbs" | yes | — |
| "Redo beat 2: cut the aside" | yes | `REDO_CHANGE` |
| "Make Zara colder", "Redo beat 2.", "Add a cutting remark." | no | `REDO_DIRECTION` on the redo |

Other prompts are byte-identical. **Tests**: `test_scene_revise.py`.
