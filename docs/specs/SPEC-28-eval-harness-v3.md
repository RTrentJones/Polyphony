# SPEC-28 — Eval harness v3: fair caps, a consistent fixture, contracts for wave 2

**Closes**: W2-11 (H1–H3, F8; F7 in the harness) · **Size**: M · **Depends on**: the day-0
contracts (`app/planning/planned_beat.py`, tpm-plan-wave2 §1.3). Codes against the §1.3 strings
only; it needs no other wave-2 package to land first.

## Why

The wave-1 relay run showed three ways the eval misread the product:

1. **H1. The R1 cap was binary and matched names only.** After-run scene 1 has Zara speak as "the
   woman at the next counter", correctly, before Milo learns her name. The plan named her and the
   prose did not, so R1 was set to 0 and the scene scored 0, although the judge gave R1 0.88.
2. **F8 / H2. The premise fixture contradicted the synopsis.** `premise_edit.premise_restated`
   ended "reconcile Edric's account and end him"; the synopsis has Edric "files it correctly, and
   goes wherever it is the dead go". R6 graded the outline against two endings.
3. **H3. No checks existed for this wave's fixes** (F1, F2, F3, F4, F5, F7, F9). The eval also
   stored the cel voice as `{"voice": …}`, which rendered "Voice: voice: …" in every canon block
   (F7).

## Outcome

The R1 cap fires only on a principal who is truly absent. R6 grades against one ending. Every
wave-2 prompt contract (P1–P10) and the §5.2 step checks are deterministic numbers, and an archive
from before a contract existed still grades (`rate: None` where nothing was checked).

## Scope

**In**: `evals/graders/{prompt_contract,rubrics}.py`, `evals/steps/pipeline.py`,
`evals/harness/{client,report}.py`, new `evals/harness/rescore.py`,
`evals/corpora/cel/ground_truth.json`, tests `tests/unit/test_prompt_contract.py`,
`tests/unit/test_evals_steps.py`, new `tests/unit/test_evals_rescore.py`.
**Out**: `evals/stub_llm.py` (E2), any product behaviour, `evals/README.md`.

## Design

### H1: the R1 cap (`_grade_scene_checks`, `speaking_principals`)

- A principal is **present** when named (as before) **or** credited with a quoted line.
- Credit comes from the SPEC-17 attributor (`app.parsing.character_extractor.attribute_speech`,
  read-only), plus `descriptive_tags` for descriptors longer than the attributor reads (it takes a
  determiner and up to three words, so "the woman at the next counter said" credits nobody there).
  - **tag**: the tag subject matches one principal's forms (`speaker_matches`, honorific-aware, so
    "Ms. Okafor said" is Zara).
  - **unnamed**: a lower-case descriptor speaks in an inner beat whose cast (from the grounding
    record, `GET /books/scenes/{id}/grounding`) holds exactly one principal that beat never names,
    and that descriptor never spoke in a beat without that principal. "The caseworker" of an
    earlier Milo-only beat is therefore never Zara. Without per-beat casts there is no unnamed
    credit.
- **Cap** (R1 = 0) only when a principal the scene's own plan names is neither named nor speaking,
  or when the scene has a cast and none of it is present. A principal who speaks but is never named
  goes to `deterministic_warnings`, and the judge's R1 stands.
- `scene["characters"]` is the scene's own cast (after SPEC-23, the beat cast). `_collect_scene`
  records the request's `planned_beat_id`, `setting`, `emotional_tone`, `pov_character`, and each
  inner beat's cast. The prose artifact records `chapter.planned_beats` (cast as names), and the
  judge's scene content carries `PLANNED BEAT`, `BEAT CAST`, `BEAT SECONDARY PEOPLE`, `SETTING`,
  `TONE` and `POV CHARACTER` (empty fields omitted).
- The prose cache key is `planned-v3`: a v2 entry lacks the beat data and is regenerated.

### F8 and F7 (fixture and harness)

- `premise_edit.premise_restated` now ends: "…reconcile Edric's account, and Edric, undone by his
  own terms of service, files it correctly, and goes onward."
- `fidelity.secondaries: ["Prava", "Old Fen"]`: the synopsis' named secondary people (not cast,
  both already in `fidelity.known`).
- `_cast_with_roles` stores the voice as `{"diction": …}`.

### Prompt-contract checks (§5.1; `evals/graders/prompt_contract.py`)

Each must be 100% of checked calls. The six wave-1 checks stay.

| Check | Purposes | Pass |
|---|---|---|
| `outline_secondaries_allowed` | outline:chapters, outline:beats | P1 line lists every `fidelity.secondaries` name; `Principal characters MUST be canon cast` present; `Every name you use for a character MUST be one of` absent |
| `beats_stage_fields` | outline:beats | P2: a children template object holds `"characters"`, `"secondary_names"`, `"setting"`, `"tone"` |
| `plan_cast_from_beat` | plan_beats | the P3 cast (aliases resolved) = the outline beat's cast (else the chapter's) + chapter POV. Beats matched by `Scene description:` = `title: summary` of an `outline:beats` answer child. Unmatched prompts are listed in `unmatched`, not failed |
| `plan_cast_available_not_exact` | plan_beats | P3 line, `unnamed functional figures`, no `cast is exactly` |
| `no_blank_setting` | plan_beats, beat_prose | no `^\s*Setting:\s*$` |
| `no_neutral_tone` | plan_beats, beat_prose | only when style.json has a tone: no `Emotional tone: neutral` |
| `pov_line_has_mode_and_character` | beat_prose | P5 line; `who` = the chapter POV (the outline answer's chapter whose summary is in `<chapter>`, else style.json's "on X"); `Narrate only what <who> … perceives or knows` in the same paragraph. With no POV, `Point of view: <mode>.` passes |
| `last_beat_in_pov` | beat_prose, last beat per `<beats>` | the P6 text |
| `no_duplicate_bible` | beat_prose | no `Role:/Description:/Goals:/Arc:/Voice:` line in `CHARACTERS IN THIS BEAT`; each corpus character description appears exactly once |
| `no_doubled_voice_label` | every call | no `(?i)\bvoice:\s*voice:` |
| `reduce_evidence_depth` | extraction_reduce | each principal `### Name` section the prompt has: ≥ 3 lines `- (part N)` or `- "…"` |

Also reported, not gated: `beat_prose_prompt_chars_median`. `grade(…, **corpus_inputs(gt,
style))` builds the corpus inputs. A check with no matching call reports `n = 0, rate = None` and
does not count toward `score`. After the F8 edit, `beats_stage` also accepts the premise that the
run's own `outline:chapters` prompt confirmed, so an older archive still grades.

### Deterministic step checks (§5.2)

- **Extraction**: `principal_fields` ("k/3": description, goals and voice_characteristics all
  non-empty), `principal_fields_missing`, `principal_mentions`, `mention_order_ok` (fidelity
  principal order, cel Milo ≥ Zara ≥ Edric; None when a principal is not proposed).
- **Outline**: `outline_names_synopsis_secondaries` ("k/2", from the outline text and any
  `secondary_names`), `outline_secondaries_named`.
- **Prose**: per scene `planned_beat`, `cast_from_beat` (scene cast = beat cast, else chapter cast,
  plus a secondary that now resolves, plus the chapter POV; None when no beat matches), `speaking`,
  `cast_present`, `deterministic_warnings`; step level `cast_from_beat` (rate), `r1_capped_scenes`,
  `planned_beats`. Seam overlap is unchanged.
- **Revise** (new step `revise`, after `prose`): three revisions of the prose step's first scene
  when it lives in this app, else of a scene created from its prose (or the excerpt) with no beats:
  "Tighten this paragraph" (paragraph 2), "Make Zara colder in this exchange" (her first quoted
  paragraph and the next), "Redo beat 2" (`beat_index: 1`, only with beats). Each proposal is
  checked (`outside_identical`: the text outside the range is byte-identical; `new_names`: capitalised
  names in the span not in canon, synopsis or the draft), judged on **RA** (new rubric: instruction
  adherence) and **R5** (span continuity), then discarded. Caps: outside text changed → RA = 0; a
  new name → R5 = 0. Score = mean over revisions of mean(RA, R5). A request the API refuses
  (e.g. 409 "The beat was edited") scores 0 with its `error`, and the other revisions still run.
  Job polling treats `succeeded` (the jobs table's word) as terminal.

### Re-scoring a finished run (`python -m evals.harness.rescore`)

Re-grades a report's prose step with the current checks without generating or judging. It reads
the scene text and raw judge scores from the judge archive (`SCENE TO SCORE:`), each inner beat's
cast, plan line and prose from the model archive's `beat_prose` exchanges, and re-runs the prompt
contract over the model archive.

## Results: the after-wave-1 run, re-scored

`python -m evals.harness.rescore /tmp/claude-0/eval-out/after/eval-report-cel.json --relay
/tmp/claude-0/q-after/model/answered --judge /tmp/claude-0/q-after/judge/answered`

| | Published | Re-scored (H1) |
|---|---|---|
| Scene 1 | R1 capped (0) → 0.000 | R1 0.88, warning "Zara Okafor speaks (unnamed tag) but is never named" → 0.748 |
| Scenes 2, 3 | 0.8954, 0.893 | unchanged |
| Prose score | 0.5961 | **0.8455** |
| R1 mean | 0.6333 | **0.9267** |
| R1-capped scenes | 1 | **0** |

Prompt contract on the same archive: the six wave-1 checks are all 1.0; the eleven new checks are
0.0, except `no_doubled_voice_label` 0.13 (3/23: only the calls without a canon block); score
0.3606. Median beat_prose prompt: 29,626 chars (the baseline for the ≥ 15% reduction). This is the
"before" for wave 2.

## Acceptance

- [x] `_grade_scene_checks`: an unnamed principal who speaks in an attributed line → no cap and a
  warning; a principal absent entirely → cap; a name-only miss ("Ms. Okafor said") → a warning.
- [x] The cel premise contains "files it correctly" and "goes onward"; `fidelity.secondaries` is
  Prava and Old Fen.
- [x] One passing and one failing fixture per new prompt-contract check (P1–P10).
- [x] `grade()` on an archive with none of the new purposes returns `rate: None` for them.
- [x] The voice is stored as `{"diction": …}`.

## Docs (BRD / feature-set delta for the orchestrator)

- **BRD** (evals section): the scene R1 cap is "a planned principal neither named nor the
  attributed speaker of a quoted line, or no cast on the page"; a principal who speaks unnamed is a
  recorded warning, not a cap. The eval adds a `revise` step (RA instruction adherence + R5,
  outside-range bytes, no new names).
- **feature-set** (evals): prompt-contract checks for P1–P10 (secondaries, beat fields, beat cast,
  available-not-exact, blank setting, neutral tone, POV line, last beat in POV, single bible, no
  doubled label, reduce evidence); extraction depth and mention order; outline secondaries;
  cast-from-beat; `evals.harness.rescore` for re-grading a finished run.
