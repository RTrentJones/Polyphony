# SPEC-77 — Manner eligibility: the speech-beat rule (wave 11, item 1)
**Closes**: the W10 hold's P0 item 1 (PM conditions 1–2, G-S0, G3) · **Size**: S · **Depends on**: SPEC-09, SPEC-17, SPEC-40, SPEC-69.
- **The rule.** A movement row is withheld `restates` when a line of the scene's **own** plan (brief and beat lines)
  restates it or, unless it is a **speech beat**, any line of `scope.plot` (another scene's brief, the book plan, the
  chapter's earlier beats): `any(restates(text, p) for p in (scope.own_plot if beat else scope.plot))`, one call. No new
  rule name or marker; the record, export and grader are unchanged; `restates` and `later_plot_from` are untouched.
- **A speech beat** is a `paragraph_{i}` row, i in `scope.speech_paragraphs[source_id]`: paragraphs with a span that
  `is_speech` (credited, or 3+ words: the indexer's rule; ruling (b), R-GD-1). The PM's `narration_of(p) != p` means this.
- **Scope.** It acts only on `paragraph_` rows: `narration_` rows (own narration, SPEC-09) return at `_own_narration`
  before `restates`. Latent: the indexer still writes a narrator's leftover speech-paragraph narration as `paragraph_{i}`.
- **`SceneScope`** gains both, required by `.of`; `scene_setup` builds them; `.planned(lines)` alone adds beat lines.
- **G-S0** (`python -m evals.measure.manner_sweep`: the product's `_withheld_by`, 17 signed cel archives, every store line
  of each shown owner, no threshold or k): 5 gains (Zara's p2: W9b 3, W10 2), 0 leaks; W8c credited; W10b unchanged.
- **G-D failed its pre-fixed bar (1/2 = 0.50 < 0.85) and is thin (n = 2); it must never be cited as passed.** dracula's 3
  `paragraph_` lines (p9, p192, p271) are 0 under `is_speech` (145 speech paragraphs). The PM's context read of the 72
  narration-bearing speech paragraphs is 0.57 beats (0.59 under `is_speech`). The rule's evidence is cel alone.
- **Tests.** (F) W10 chain: p2 not withheld, shown in 0027–0030, 0034, 0035 (`w10_withheld_after`); a speech beat only
  another scene plans is shown; R-GD-1; the sweep on W10; dracula lists 0. (R) via `planned` it is withheld; W8c; p0, p13;
  p19, the named residual (a speech beat that is the event), fails `samples_not_plot`; `scene_setup`'s `own_plot` and
  str-keyed `speech_paragraphs`; the probe caller passes `(), {}` (inert: its plot is empty).
