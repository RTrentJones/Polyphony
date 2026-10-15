# SPEC-18 — Beat-level prose prompt and outline beats prompt

**Closes**: B5 (beat-level prose prompt), B11 (outline beats stage sees the plan), and the
orchestrator relay finding "prior-scene tail loses paragraph breaks and is cut mid-sentence"
· **Size**: M · **Depends on**: — · **Package**: W1-C

## Problem

What reached the model, traced from the assembled prompts:

- **Prose, per beat** (`app/orchestration/prose.py::write_beat_prose`). The model saw only
  `BEAT TO WRITE: The silence after.` It did not see the scene's other beats, so it could not know
  where its beat ended and routinely played out the next one. `<prior>` was rebuilt as
  `" ".join(text.split()[-500:])`. That flattened every paragraph break into one wall of text,
  which the model then imitated, and usually opened mid-sentence. The house style ("description
  and action carry the scene; dialogue PUNCTUATES it") was hard-coded, so it overrode a
  dialogue-driven book's style guide and sample prose. Beats were joined with no seam control,
  so a beat that restated the last line of the previous one reached the author unedited.
- **Prior-scene tail** (`app/jobs/handlers.py::_scene_inputs`, ensemble). The same flattening
  was applied to the tail of the previous scene handed to the next scene.
- **Outline beats stage** (`app/planning/staged_outline.py::_stage_beats_for_act`). The prompt
  did not contain the confirmed `premise_restated`, the central conflict, or the act's goal and
  turn. Only the chapters stage had them, so beats drifted from the premise the author had agreed.
  Acts were numbered from 0 ("chapters of act 0"), and the chapters were embedded as a Python
  repr (`[{'title': …}]`) while the model was asked to answer in JSON. The chapters stage
  embedded its acts the same way.

## Behaviour

### Prose (B5)

1. **The whole beat plan.** `write_beat_prose(..., *, all_beats=None, beat_index=None,
   style=None)` renders the scene's beats, numbered from 1, inside an `as_quoted_block(...,
   "beats")` fence. The current beat is marked `→`. The prompt then says "Write ONLY beat N. Stop
   where beat N+1 begins; do not anticipate it." On the last beat it says "Write ONLY beat N, the
   last beat: end the scene here." The plan comes before `CHARACTERS IN THIS BEAT`, so it never
   mixes into the beat's cast block. New parameters are keyword-only, so the existing positional
   call still works and renders no plan.
2. **`prose_tail(text, words=PREV_TAIL_WORDS)`.** Whole paragraphs are taken from the end until at
   least `words` words are kept, and `\n\n` breaks are preserved. If the earliest kept paragraph
   is longer than the budget that remains, it starts at the latest sentence boundary that still
   meets the budget. The tail never opens mid-sentence. A boundary is `. ! ? …`, optionally
   followed by closing quotes or brackets, then whitespace and a capital or an opening quote.
   `Mr.`, `Dr.` and similar are not boundaries. This is used for the in-scene prior
   (`prose.py`, `ensemble.py`) and for the prior-scene tail (`handlers._scene_inputs`).
3. **The author's style comes first.** When the style guide has sample prose, the prompt says
   "Match this sample's voice and rhythm" and fences the sample as `<sample_prose>`, ahead of
   the writing instructions. If the guide has a tone but no sample prose, the tone replaces the
   house style. The generic craft text (`GENERIC_CRAFT_GUIDANCE`) appears only when both the
   tone and the sample prose are empty. Universal format rules (distinct voices; no headings,
   labels or `Name:` prefixes) always apply. Per-scene author style notes still override the
   guide.
4. **Seam control.** `<prior>` is introduced with "do not restate anything in <prior>, begin with
   what happens next". After generation, `trim_seam_repeat(prose, prior)` drops a beat's opening
   sentence when it exactly repeats the last sentence of `<prior>`. The comparison ignores case,
   punctuation and spacing, and the sentence must be at least 4 words long. The trim is
   deterministic and conservative: it never empties a beat and never touches anything past the
   seam. It runs in both the prose and ensemble workflows, so the trimmed text is what is
   checkpointed, stored on `SceneBeat`, and shown to the author.
5. **Checkpoints.** `_canon_hash` hashes the tail's last `PREV_TAIL_WORDS` words with whitespace
   normalised. That is exactly the old flat tail, and `prose_tail` always ends with those same
   words, so a job paused before this change resumes from its checkpoint and does not restart.
   As before, editing the prior scene inside its last 500 words still restarts the job.

### Outline beats (B11)

1. `_stage_beats_for_act(canon, act_index, act_chapters, user_id, *, skeleton=None)` includes:
   - the premise, marked "(confirmed by the author: treat it as authoritative)" under the same
     rule as the chapters stage;
   - the central conflict;
   - "This is act N of M: <title>", "Act N goal: …", and "Act N turn (… the final chapter's
     beats must land it): …".

   `generate_staged_outline` passes the skeleton through `_stage_beats`. That skeleton is the
   author-confirmed one when the plan was confirmed.
2. Acts are numbered from 1 in all prompt text. The stored `act_index` stays 0-based. The
   chapters stage embeds each act as `{"act": N, "act_index": N-1, …}`, so the mapping is
   explicit.
3. Embedded structures are serialised with `json.dumps(..., ensure_ascii=False, indent=1)`. The
   beats-stage output template uses unquoted placeholders (`"title": <chapter title>`), so the
   template is not mistaken for data.

Stub markers kept (`evals/stub_llm.py`): `"scene beats"` and `"mirroring the chapters"` (beats
stage), and `"CHARACTERS IN THIS BEAT"` and `"BEAT TO WRITE"` (prose).

## Tests

- `tests/unit/test_prose_prompt.py` covers:
  - `prose_tail`: paragraphs kept; the budget met at the latest sentence boundary; never
    mid-sentence, including after closing quotes and abbreviations; equivalence with the old tail
    and its hash.
  - `trim_seam_repeat`: trims an exact repeat; keeps different or short openings; never empties a
    beat.
  - The prompt: the `<beats>` block with `→` and the stop line; the last-beat line; `\n\n` in
    `<prior>`; sample prose leads and no house style; tone alone replaces house style; house style
    only when both are empty; the positional call.
- `tests/unit/test_scene_canon.py` drives the prose workflow end to end and checks:
  - every beat prompt has the plan marked;
  - the prior beat's paragraph break reaches the next prompt;
  - the book's sample prose leads every beat prompt;
  - a seam repeat is trimmed from the stored `SceneBeat` and from `Scene.content`.
- `tests/unit/test_incremental_resume.py`: `_scene_inputs` keeps paragraph breaks, starts on a
  sentence, and still hashes equal to the old flat tail.
- `tests/unit/test_staged_outline.py` covers:
  - the beats prompt has the premise, the central conflict and the act's own goal and turn;
  - "act 1" appears, never "act 0";
  - a confirmed premise is authoritative in the beats stage;
  - no `{'` or `['` appears in any staged-outline prompt;
  - the embedded chapters parse as JSON.
- `scripts/e2e_stub.sh` passes unchanged, which shows the stub markers survive.

The judge-scored acceptance runs in the after-eval: R3 beat adherence ≥ 0.8, R4 style ≥ 0.8, a
deterministic seam 6-gram overlap of 0, and outline R6 ≥ 0.8 with R1 = 1.0 on the author-edited
premise run.

## BRD / feature-set delta (for the orchestrator to apply)

**docs/BRD.md, §5.1, after R1.2:**

> - **R1.2a** Each outline stage sees the plan it details. The beats stage receives the premise
>   (authoritative when the author confirmed or edited it), the central conflict, and its act's
>   title, goal and turn. Acts are numbered from 1 in prompt text. Structures embedded in a
>   prompt are JSON, never a language-specific repr.

**docs/BRD.md, §5.1, after R1.1 (scene prose):**

> - **R1.1a** A beat is written as that beat. The prose prompt carries the scene's whole beat
>   plan with the current beat marked, and tells the model to stop where the next beat begins.
>   Prior text (the scene so far, and the previous scene's tail) keeps its paragraph breaks and
>   never opens mid-sentence. The book's sample prose is the primary style reference. Generic
>   house style is used only when the style guide has neither tone nor sample prose. A beat's
>   opening sentence that exactly repeats the end of the prior text is trimmed (deterministic,
>   never empties a beat).

**docs/feature-set.md, add these rows near "Staged outline":**

| Feature | BRD | Status | Where |
|---|---|---|---|
| Beat prompt sees the whole beat plan (current beat marked, stop line) | R1.1a | ✅ | `app/orchestration/prose.py::write_beat_prose` (SPEC-18) |
| Paragraph-preserving prior tail, never mid-sentence | R1.1a | ✅ | `prose.py::prose_tail`, used by prose, ensemble and `handlers._scene_inputs` (SPEC-18) |
| Sample prose leads style; house style only when the guide is empty | R1.1a | ✅ | `prose.py::_style_section` (SPEC-18) |
| Deterministic seam-repeat trim between beats | R1.1a | ✅ | `prose.py::trim_seam_repeat` (SPEC-18) |
| Outline beats stage sees premise, conflict and act goal/turn; JSON, acts from 1 | R1.2a | ✅ | `app/planning/staged_outline.py::_stage_beats_for_act` (SPEC-18) |
