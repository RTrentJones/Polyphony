# SPEC-72 — Placement for chapters ≥ 2, measured (wave 11, item 2)
**Closes**: the W10 hold's item 2 for this wave (measurement only) · **Size**: XS · **Depends on**: SPEC-40.
- **What is measured.** Today's later-plot cut, `later_plot_from(cel paragraphs, chapter, the run's outline)`, against
  the signed gold's tail (the first paragraph of the first event the PM placed after `chapter`), for chapters 1–4 of each
  signed archive's outline: the principal's 72 cells (W4b2 to W9b, 18 archives) plus W10 and W10b's 8 (added by hand).
- **The table** (cells equal / cut earlier than gold / later than gold; "none" counts as after every paragraph):

  | Chapter | 18 archives | + W10, W10b | 20 archives |
  |---|---|---|---|
  | 1 | 18 / 0 / 0 | 2 / 0 / 0 | **20 / 0 / 0** |
  | 2 | 6 / 12 / 0 | 2 / 0 / 0 | 8 / 12 / 0 |
  | 3 | 5 / 10 / 3 | 2 / 0 / 0 | 7 / 10 / 3 |
  | 4 | 6 / 6 / 6 | 2 / 0 / 0 | 8 / 6 / 6 |
  | All | **35 / 28 / 9** of 72 | 8 / 0 / 0 | **43 / 28 / 9** of 80 |

- W10's cuts are 9 then none; W10b's 14 then none: both equal their signed gold in every chapter.
- **No rule changes this wave.** Prose stays pinned to chapter 1, where placement is gold-perfect; `later_plot_from` is
  untouched. The chapters ≥ 2 rule, and `placement_sweep.py` as its first user, go to wave 12 by name.
