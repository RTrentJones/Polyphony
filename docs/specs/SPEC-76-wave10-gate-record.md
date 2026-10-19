# SPEC-76 — Wave-10 merge gate record (the hold), and the speech-beat rule's footing
**Closes**: wave-10 §6, its code gate's F2, the PM's S0 (d) · **Size**: docs · **Depends on**: SPEC-72 (its 72-cell table), -73, -75, -77.
- **Draws** (7c581b3, `prose_chapter` 1): W10 `gated-full`, W10b `gated-scenes`; run-W10a skipped (SPEC-74). **W10b passes
  every row.** **W10 fails movement 8/12**, `ok` false: Zara's p2 is "eligible, withheld (restates)" in blocks 0027, 0029, 0030
  and 0035, as `restates` read the whole book's plan (scene 1 restates p2). Not placement (the principal's eval review).
- **PM: held**: the third failed movement draw running, in W8c's family; pre-existence (86fab95) argued by code identity, not
  shown by a regrade. Wave 10 merges only combined with wave 11, as one PR, once the PM's four merge conditions hold (SPEC-79).
- **F2, W9b's regrade (wave-10 tree):** `secondary_has_context` n = 0, `exempt` set; Zara cast, starved 0/0/9; `ok` false;
  `not_cast` [Edric]; W9 stays `ok` true, not exempt. **SPEC-73's eval side (E1):** the outline book ingests the source first, cached with
  the outline (`OUTLINE_CACHE_KEY` bumped); G3 passed (prompts byte-identical). Outline sourcing is **cel-only**, keyed on `source_facts`.
- **Speech-beat rule (SPEC-77).** Provenance: the PM's wave-8 signing criterion, written before this case. Evidence: **one corpus**,
  24 narration paragraphs, 5 gains (14 of 15 by the gold); G-S0 5 gains / 0 leaks, W8c credited, W10b unchanged. Not a general law.
- **G-D: "one corpus; the dracula check (G-D) failed its bar at n = 2 (1 beat, 1 event) and is thin"** (0.50 < 0.85); never
  cite it as passed (dracula's 72 narration-bearing speech paragraphs, context: 0.57 beats). **Design gate, (b) narrow:** a speech
  paragraph has a span that `is_speech` (credited, or 3+ words), the indexer's predicate. Cel's 22 are unchanged, so G-S0 is
  byte-identical; dracula's 177 become 145, and its exposure is 0 lines. That is no G-D pass: the premise does not carry to
  dracula (0.57), and `paragraph_` rows of long third-person speech paragraphs are unmeasured (wave 12, before any non-cel draw).
- **Named residuals.** **p19** (cel): a speech beat that is the event, guarded by `samples_not_plot` in a gated draw; the (R) row
  pins that it fails. **R-GD-1** (dracula p271, Verhoeven): an event, closed by construction under `is_speech`; p192 is inert, not
  a residual. Open (wave 12): the indexer's narrator path still writes leftover narration as `paragraph_{i}`.
