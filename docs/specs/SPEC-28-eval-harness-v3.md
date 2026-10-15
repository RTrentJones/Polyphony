# SPEC-28 — Eval harness v3: fair caps, a consistent fixture, contracts for wave 2

**Closes**: W2-11 (H1–H3, F8; F7 in the harness); wave 2.5 package C (review B1, S4, S5, S10,
W1-S7, W1-S9, W1-S11, PR-S5, PR-S6, PR-C2, PR-C5, Coulds 2 and 6) · **Size**: M · **Depends on**:
the wave-2.5 day-0 contracts (`app/core/prompt_markers.py`, `classify_people`), and E's `beats` on
`GET /api/v1/scenes/{id}`.

**Wave 2.5 (hardening).** The grader cannot drift from the app (the round trip), checks behaviour
rather than wording, R1 uses the simple rule (S4), the harness reads the app through its API only,
and the dead grader paths and the rescore tool are gone. The sections below describe the current
design; the wave-2 H1 rule and the rescore tool are recorded under "History".

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

**In**: `evals/graders/prompt_contract.py`, `evals/steps/pipeline.py`,
`evals/harness/{client,config}.py`, `evals/corpora/cel/ground_truth.json`, `evals/README.md`; tests
`test_prompt_contract.py`, `test_evals_steps.py`, `test_evals_client.py` and the new
`test_prompt_roundtrip.py`. **Out**: `evals/stub_llm.py` (package A), any product behaviour.

## Design

### S4: the R1 cap (`_grade_scene_checks`, `named_in`)

- `named_in(text, forms)`: a capitalised run of the text ("Ms. Okafor", "Zara's") names one of the
  forms by `app.parsing.names.match`, so honorifics and surnames count.
- **Cap** (R1 = 0) only when the scene has a cast and none of it is named.
- A principal the scene's own plan (brief and beat descriptions) names who is never named on the
  page is a warning, `"<name> is in the plan but never named"`, and the judge's R1 stands.
- PR-C5: `names.match` on a bare surname can credit the wrong person ("Thane" for any Thane). That
  is acceptable for a warning.
- Deleted: `speaking_principals`, `descriptive_tags`, `_descriptor`, `_speaker_principal`,
  `_PERSONAL_SPEAKERS`, `_beat_casts` (the grounding read), the `speaking` and `cast_present`
  fields, and the `_TAG_VERBS` import.
- `_collect_scene` records the request's `planned_beat_id`, `setting`, `emotional_tone`,
  `pov_character`, and each inner beat (`beat_number`, `description`, `content`) from
  `GET /api/v1/scenes/{id}` `beats` (W1-S9). The judge's scene content carries `PLANNED BEAT`,
  `BEAT CAST`, `BEAT SECONDARY PEOPLE`, `SETTING`, `TONE` and `POV CHARACTER` (empty fields
  omitted). The prose cache key is `planned-v3`.

### The harness reads the app through its API (W1-S9)

`client.scene_beats(scene_id)` returns `GET /api/v1/scenes/{id}`'s `beats`. The raw `SELECT` on
`scene_beats`, `_ENGINES`, `EvalConfig.database_url`, `EVAL_DATABASE_URL`, the grounding fallback
and `client.scene_grounding` are deleted.

### F8 and F7 (fixture and harness)

- `premise_edit.premise_restated` now ends: "…reconcile Edric's account, and Edric, undone by his
  own terms of service, files it correctly, and goes onward."
- `fidelity.secondaries: ["Prava", "Old Fen"]`: the synopsis' named secondary people (not cast,
  both already in `fidelity.known`).
- `_cast_with_roles` stores the voice as `{"diction": …}`.
- `pov: "Milo Voss"` (PR-C2): the expected POV character, so the grader no longer parses the style
  guide's POV a third time (`pov_character` is deleted).

### Prompt-contract checks (`evals/graders/prompt_contract.py`)

The grader imports every string it parses from `app/core/prompt_markers.py`. `purpose()` returns
the archived `X-Polyphony-Purpose` header (the `_PURPOSE_MARKERS` inference is deleted: every
archive carries the header). Fourteen checks, each 1.0 of the calls it checks:

| Check | Purposes | Pass |
|---|---|---|
| `beats_block` | beat_prose | a numbered `<beats>` list, and "Stop where beat N+1 begins" unless the marked beat is last |
| `prior_paragraphs` | beat_prose | a `<prior>` of more than 200 words (longer than any house-prose paragraph) holds a `\n\n`, and it starts a sentence (W1-S11; `ProseStream` is deleted) |
| `voice_dialogue` | any call with voice samples | every sample is the character's own line in `gold_lines` |
| `beats_stage` | outline:beats | the premise, central conflict and an act's goal and turn are in the prompt; acts from 1; no `{'` |
| `outline_no_repr` | outline:* | no `{'` |
| `style_first` | beat_prose | the style sample prose is in the prompt and the generic craft text is not |
| `plan_cast_from_beat` | plan_beats | the `Available cast` line parses to someone (a missing or empty line fails), and equals the corpus cast among the outline beat's `people` (falling back to `characters`; else the chapter's) plus the chapter POV. Beats are matched by `Scene description:` = `title: summary`; a prompt that matches none is listed in `unmatched` |
| `no_blank_setting` | plan_beats, beat_prose | no `^\s*Setting:\s*$` |
| `no_neutral_tone` | plan_beats, beat_prose | only when the style has a tone: no `Emotional tone: neutral`, unless the matched outline beat's own tone is "neutral" (S7: an author's deliberate "neutral" is kept; only a blank tone is omitted) |
| `pov_line_has_mode_and_character` | beat_prose | `POV_LINE_RE` parses; `who` is the chapter POV (the outline answer's chapter whose summary is in `<chapter>`, else ground truth `pov`); "Narrate only what <who> … perceives or knows" follows. With no expected POV, the mode alone passes. "Dr. Ada Vance" is read whole (S5) |
| `last_beat_no_wrap_up` | beat_prose, last beat | no "end the scene here" (case-insensitive). A tripwire: delete in wave 3 |
| `no_duplicate_bible` | beat_prose | no bible line in `CHARACTERS IN THIS BEAT`; each corpus description exactly once |
| `no_doubled_voice_label` | every call | no `voice: voice:` |
| `reduce_evidence_depth` | extraction_reduce | each principal section: ≥ 3 evidence or quoted lines |

Retired (S5; their behaviour is graded elsewhere): `outline_secondaries_allowed` (the SECONDARY
list is gone, S3; the outcome is `outline_names_synopsis_secondaries`), `beats_stage_fields`
(template keys; the outcome is `cast_from_beat`) and `plan_cast_available_not_exact` (its wording
half was B1; its behaviour is in `plan_cast_from_beat`).

Reported, not gated: `beat_prose_prompt_chars_median` and `voice_dialogue.short_samples` (voice
samples under three words, finding 5). A check with no matching call reports `n = 0, rate = None`
and does not count toward `score`.

### The round trip (B1, PR-S5; `tests/unit/test_prompt_roundtrip.py`)

A capturing fake LLM records each `(messages, purpose)` and answers with `evals.stub_llm._answer`,
which also proves the stub parses the real prompts. For a cel-like book (the cel cast, style and
synopsis in the unit-test DB) it drives public entry points only: `generate_staged_outline(canon)`
(skeleton, chapters, beats), `plan_scene_beats` for a stored outline beat with its setting and
tone, `run_prose_scene_workflow` for a whole scene (three beats; `resolve_pov` reads the style
POV) and `extract_canon` on the cel excerpt. The records are written as an archive and graded:
every check that ran is 1.0, and all fourteen run (n > 0). A failure is fixed in the prompt that
broke the contract, never in the check. `test_prompt_contract.py` keeps one failing fixture per check.

### Deterministic step checks (§5.2)

- **Extraction**: `canonical_names_ok` ("k/n" over the principals with a title alias that does
  not name them, e.g. Edric's "Chief Executive Lich": the proposal is named for the person, not
  the title; reported, finding 4), `principal_fields` ("k/3": description, goals and voice_characteristics all
  non-empty), `principal_fields_missing`, `principal_mentions`, `mention_order_ok` (fidelity
  principal order, cel Milo ≥ Zara ≥ Edric; None when a principal is not proposed).
- **Outline**: `outline_names_synopsis_secondaries` ("k/2", from the outline text and any
  `secondary_names`), `outline_secondaries_named`.
- **Prose**: per scene `planned_beat`, `cast_from_beat` (scene cast = beat cast, else chapter cast,
  plus a beat secondary name that `classify_people` now resolves to the cast, plus the chapter
  POV; None when no beat matches), `deterministic_warnings`; step level `cast_from_beat` (rate), `r1_capped_scenes`,
  `planned_beats`. Seam overlap is unchanged.
- **Revise** (new step `revise`, after `prose`): three revisions of the prose step's first scene
  when it lives in this app, else of a scene created from its prose (or the excerpt) with no beats:
  "Tighten this paragraph" (paragraph 2), "Make Zara colder in this exchange" (her first quoted
  paragraph and the next), "Redo beat 2" (`beat_index: 1`, only with beats). Each proposal is
  checked (`outside_identical`: the text outside the range is byte-identical; `new_names`:
  `fidelity.unknown_proper_nouns` over the span, with the synopsis and the draft as `canon_text`), judged on **RA** (new rubric: instruction
  adherence) and **R5** (span continuity), then discarded. Caps: outside text changed → RA = 0; a
  new name → R5 = 0. Score = mean over revisions of mean(RA, R5). A request the API refuses
  (e.g. 409 "The beat was edited") scores 0 with its `error`, and the other revisions still run.
  Job polling treats `succeeded` (the jobs table's word) as terminal.
- **Name invention** (`unknown_rate_canon` / `unknown_canon`, W1-S7): the app's own
  `fidelity.unknown_proper_nouns(text, known, canon_text=synopsis)`, with the corpus' known terms
  and cast as `known`. The eval's lowercase rule and `_SENTENCE_WORDS` are deleted.

## Results

### The after-wave-1 run, re-scored with S4 (the scene baseline)

Re-scored once with the S4 rule before `rescore.py` was deleted; the output is
`/tmp/claude-0/eval-out/after-rescored/rescored-s4.json` (PR-S6: the scene R1/R4 baseline).

| | Published | Re-scored (H1, wave 2) | Re-scored (S4) |
|---|---|---|---|
| Scene 1 | R1 capped (0) → 0.000 | 0.748 | R1 0.88, warning "Zara Okafor is in the plan but never named" → 0.748 |
| Scenes 2, 3 | 0.8954, 0.893 | unchanged | unchanged |
| Prose score | 0.5961 | 0.8455 | **0.8455** |
| R1 / R4 mean | 0.6333 / 0.8833 | 0.9267 / 0.8833 | **0.9267 / 0.8833** |
| R1-capped scenes | 1 | 0 | **0** |

### The q-w2 archive, regraded

The frozen after-wave-2 archive (27 calls), graded by the day-0 grader: 1.0 on all 16 checks that
ran (`plan_cast_from_beat` n = 0). By the hardened grader: see the wave-2.5 report.

## Acceptance

Wave 2:
- [x] The cel premise contains "files it correctly" and "goes onward"; `fidelity.secondaries` is
  Prava and Old Fen.
- [x] `grade()` on an archive with none of the new purposes returns `rate: None` for them.
- [x] The voice is stored as `{"diction": …}`.

Wave 2.5:
- [x] The round trip grades the app's real prompts at 1.0 on every check that ran, with
  `beats_block`, `prior_paragraphs`, `beats_stage`, `outline_no_repr`, `plan_cast_from_beat`,
  `no_blank_setting`, `no_neutral_tone`, `pov_line_has_mode_and_character`,
  `last_beat_no_wrap_up`, `no_duplicate_bible`, `no_doubled_voice_label` and
  `reduce_evidence_depth` all n > 0.
- [x] One failing fixture per kept check.
- [x] R1: cast Milo + Zara, "the caseworker said", Zara never named → no cap, one warning; no cast
  named → cap; "Ms. Okafor said" → Zara is named.
- [x] `prior_paragraphs`: a one-paragraph prior passes; a two-paragraph prior with its break
  removed fails; a prior opening mid-sentence fails.
- [x] `scene_beats` reads `beats` from the API body; `EvalConfig` has no DB URL.

## Docs (BRD / feature-set delta for the orchestrator)

- **BRD** (evals section): the scene R1 cap is "the scene has a cast and none of it is named on
  the page"; a planned principal who is never named is a recorded warning, not a cap. The eval
  adds a `revise` step (RA instruction adherence + R5, outside-range bytes, no new names). The
  harness reads the app through its API only.
- **feature-set** (evals): fourteen prompt-contract checks, graded against the app's real prompts
  by a round-trip test (beats block, prior paragraphs, voice dialogue, beats stage, no repr, style
  first, beat cast, blank setting, neutral tone, POV line, no wrap-up on the last beat, single
  bible, no doubled label, reduce evidence); extraction depth, mention order and canonical names;
  outline secondaries; cast-from-beat. Remove `evals.harness.rescore`.

## History

- **Wave 2 (H1).** R1 credited an unnamed speaker to a principal through the SPEC-17 attributor,
  long descriptive tags and each inner beat's cast from the grounding record, and capped when a
  planned principal was neither named nor speaking. Review S4 replaced it: the inference could
  credit the wrong person (in cel the unnamed caseworker is Prava) and needed the grounding API.
- **Wave 2 rescore.** `evals/harness/rescore.py` re-derived scenes from prompt markers to re-grade
  one old run. It was run once with S4 (above) and deleted (review S10).
