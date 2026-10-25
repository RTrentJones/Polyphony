# Provenance — Wuthering Heights (renamed eval corpus)

## Source work
- **Title:** *Wuthering Heights*
- **Author:** Emily Brontë
- **First published:** 1847
- **Copyright status:** **Public domain in the United States** (published before 1929).
- **Text source:** Project Gutenberg eBook #768 (`gutenberg.org/cache/epub/768/pg768.txt`, read 2026-10-19).
  `evals/tools/rename.py` strips the Project Gutenberg header, license and trademark references
  (`strip_gutenberg`) before any text is used.

## What lives here
- **`excerpts.txt`** — a **renamed derivative** of seven chapters (1–3, 9, 15, 21, 22), not the whole
  novel: `python -m evals.tools.rename --book wuthering --source <pg768.txt>`.
- **`aliases.json`** — the rename map, the sections, and `section_aliases` (below).
- **`spec.json`** / **`ground_truth.json`** — cast, synopsis (both timelines), six canonical beats, two
  continuity injections; gold voice lines for the two narrators
  (`python -m evals.tools.build_gt --book wuthering`).

## Why this book
It is the baseline's **nonlinear manuscript** (SPEC-84's manuscript order: Frankenstein, Wuthering
Heights, cel): a dated frame narrator (Pembury, Chapters 1–3) and a second narrator (Mattie) telling
two timelines a generation apart — the mother's (Chapters 9, 15) and the daughter's (Chapters 21–22).

## Why the names are changed, and the two Catherines
Every character and place is renamed (Heathcliff → Varrick, Lockwood → Pembury, Nelly Dean → Mattie /
Martha Bryce, Thrushcross Grange → Fallowmere Grange, …) for **evaluation integrity only**: the original
names measure a model's memory of a famous novel, not the pipeline. This file credits the source plainly.
- **Linton** is one invented surname (Penhale) for Edgar, Isabella and the daughter, and also for Linton
  Heathcliff's first name (Penhale Varrick): the book's own overlap is kept.
- **Catherine / Cathy** was read occurrence by occurrence. In Chapters 1–3, 9 and 15 every one is the
  mother (Rosalind / Rosie); in Chapters 21–22 every one is the daughter. `section_aliases` renames the
  daughter there (Ottilie / Tilly), so the two are separable characters. One nuance is lost: Heathcliff's
  "Catherine Linton (the very name warms me)" no longer echoes the mother's name.

## Legal summary
The underlying work is US public domain; the Project Gutenberg wrapper is stripped; only a renamed
derivative of seven chapters is committed — never the raw source, never the whole novel.

## Rename leftovers fixed (wave 19, SPEC-94)
A stricter leak check found italicised and plural originals the whole-word rename had
missed: the ledge's "_Catherine Earnshaw_ … _Catherine Heathcliff_ … _Catherine
Linton_" and Pembury's "_Linton_ … _Earnshaw_" in Chapter 3, and "the Lintons" in
Chapter 9 (`Lintons` is now an `aliases.json` key, → Penhales). The fix re-applies the
(now italics-aware, section-aware) aliases to `excerpts.txt`; `ground_truth.json` is
unchanged (no gold or injection line held a leak).

