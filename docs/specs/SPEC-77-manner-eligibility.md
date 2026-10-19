# SPEC-77 — Manner eligibility: the speech-beat rule (wave 11, item 1)
**Closes**: the W10 hold's P0 item 1 (PM conditions 1–2, G-S0, G3) · **Size**: S · **Depends on**: SPEC-17, SPEC-40, SPEC-69.
- **The rule.** A movement row is withheld `restates` when a line of the scene's **own** plan (brief and beat lines)
  restates it or, unless it is a **speech beat**, any line of `scope.plot` (another scene's brief, the book plan, the
  chapter's earlier beats): `any(restates(text, p) for p in (scope.own_plot if beat else scope.plot))`, one call. No new
  rule name or marker; the record, export and grader are unchanged; `restates` and `later_plot_from` are untouched.
- **A speech beat** is a row at `paragraph_{i}` with i in `scope.speech_paragraphs[source_id]`; `speech_paragraphs(text)`
  is the paragraphs where `attribute_speech` (SPEC-17) finds a quoted span. The PM's `narration_of(p) != p` means this
  (the literal form flags curly-quote and whitespace artefacts: dracula 231 vs 177).
- **`SceneScope`** gains `own_plot` and `speech_paragraphs`, required by `.of` (a probe, a scene of one, passes `(), {}`);
  `scene_setup` builds them; `.planned(lines)` is the one way beat lines join `plot` and `own_plot`.
- **G-S0** (`python -m evals.measure.manner_sweep`: the product's `_withheld_by`, 17 signed cel archives with a store,
  every store line of each shown owner, no threshold or k): 5 gains (Zara's p2: W9b 3, W10 2), 0 leaks; W8c's p2 still
  withheld by its own scene's plan (credited); W10b unchanged.
- **G-D** (`--book dracula`): every speech-beat line the product indexes as movement for the corpus cast, one row with its
  paragraph: 177 speech paragraphs, 3 lines (a diary: first-person narration is own narration, never withheld).
- **Residual p19** (a speech beat that is the event): released if another chapter scene plans it; `samples_not_plot` fails.
- **Tests.** (F) W10 chain: p2 absent from Zara's withheld record and shown in 0027–0030, 0034, 0035 (`w10_withheld_after`);
  a speech beat only another scene plans is shown; the sweep on W10; the dracula list. (R) via `planned` it is withheld;
  W8c's export; Milo's p0 and p13 withheld; p19 fails `samples_not_plot`; the probe's prompt is byte-identical.
