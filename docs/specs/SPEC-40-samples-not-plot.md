# SPEC-40 — Samples show manner, never plot

**Closes**: W5-1 (a)(b)(c) · **Size**: M · **Depends on**: wave-5 day-0 (`SceneScope.plot`/`.later_from`,
`book_plan`, `earlier_beats`, `source_location`, retrieval `source_id`).

- **One event rule over the plan.** `scene_setup` builds `scope.plot` once per scene: the brief, the
  book's planned scenes (`beat_scene_description`) and the chapter's earlier written beat lines;
  prose and the ensemble add the scene's own beat lines once planned (revise: SPEC-41). A movement
  sample that `restates` (ex-`restates_brief`: ≥ `RESTATES_MIN` = 4 content words) any line is
  dropped, no refill; voice samples are not filtered by it. Trade-off: the scene's own event is no
  longer shown as "manner" in beats that do not write it, as wave 4 accepted for the brief.
- **Later plot by best match, per source.** `later_plot_from`: a paragraph's chapter is that of the
  plan line its **narration** shares the most content words with (≥ 4, ties to the earlier chapter);
  later plot begins at the first paragraph placed after the scene's `Chapter.number`. The narration
  places events; quoted speech does not. `narration_of(paragraph)` (beside `source_paragraphs`) is
  the extractor's own split, so there is one speech rule. Voice and movement samples at or past the
  cut are not shown, nor the secondaries' own lines. Sources are read once per `scene_setup`; no
  story position, no cut. Cel ch 1: W4b2 cuts at paragraph 9 ("Halvard Bell", 13 shared), W4 at 14
  ("The Memo", 5), W5 and W5b at 9, never at 3. W5b ch 2 cuts at 14. Before the narration rule W5b
  cut chapters 1-3 at paragraph 4: Milo's "any low-hanging fruit" matched its ch-4 title "Low-Hanging
  Fruit", and every voice line was dropped.
- **Introductions.** `introduction()` keeps the naming sentence through its first appositive (before
  the second comma, less a trailing "and"/"and who"/"and which"), in `scene_secondaries` only;
  `intro_sentence` and "Add to cast" are unchanged. The full Prava sentence still reaches every call
  through the canon block's synopsis (BRD R1.1): the cut changes what the prompt presents as who she
  is, it does not hide the information, and it is not a spoiler fix.

**Residual risk.** One paragraph whose narration best matches a later chapter at ≥ 4 ends that
source's usable text for every earlier chapter. Thinner movement blocks: `emptied_by_rule` (SPEC-45) separates them
from starvation. **Restart:** the introduction cut changes `setup.secondary_block`, which
`_canon_hash` hashes, so a paused job with an Also-named secondary restarts once after deploy, and
again if a plan edit moves where the source's later plot begins; `plot` is not hashed. It records
`CANON_CHANGED_NOTE`, which misnames the cause.

**Known limitation (cel, measured on the W5 and W5b stores).** Cel chapter 1: 0 movement lines for
every principal, all emptied by rule (paragraphs 0, 3, 9, 13, 27 for Milo; 3, 29 for Zara; Edric's
14 and 17 are past the cut). The outline plans every movement line the store holds for chapter 1, or the line sits
past the later-plot cut. The rule is right and the store is too thin: this is a target the wave did
not measure, not a pass of `movement_coverage` ≥ 0.8. Voice is unchanged: Milo [4, 6], Zara [5, 7].
Wave 6 (W6-1, ex W5-11) makes manner samples survive a thin store.
