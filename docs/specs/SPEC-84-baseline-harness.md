# SPEC-84 — Baseline harness and its pre-registration (W12-5 → wave 13: W13-1, the W12-9 run)
**Closes**: W12-5, W13-1 · **Size**: M · **Depends on**: SPEC-17, SPEC-40, SPEC-72, SPEC-81, SPEC-87. Pre-registration first, at day-0.
- **Slots and arms** (`evals/baseline/`, step `baseline`). **B** (today's retrieval-assisted prose) and **C** (the ensemble) run
  through the API, `mode_used` checked (LLM_TIER=paid), and are trashed once read, so no arm reads another's prose as its prior tail.
  **A** runs `run_prose_scene_workflow` in-process with `prose.build_cast_context` patched at call time (no product seam): B's beats
  seeded through the checkpoint, casts rebuilt from B's `grounding[idx]` `character_id`s, B's job request, its own job id.
- **Blinding.** Random ids; fences, `→`, beat labels, `[[eval-…]]` stripped; pair order and each slot's edit arm seeded; seed and key
  sealed outside the bundle. Sheet: preference, continuity vs the canon sheet, edit-to-acceptable.
- **Also reported:** continuity errors per scene, p50/p95 latency, requests, tokens and cost per scene (one SQL sum over
  `api_usage.job_id` per arm; cost is $0 here, unpriced tokens beside). **Tests** (F), `test_baseline_harness.py`: request diff,
  seeded beats, rebuilt casts, job-id cost; N3; the leak test; the win rates.
- **A's lines (N3, as signed):** per present character, the first 3 dialogue lines in source order, each of at least `SPEECH_MIN_WORDS`
  words and not in `scope.later_from`; no ranking, retrieval, movement or owned-phrase block. `later_plot_removed` counts candidates
  the later-plot cut dropped before the third pick (SPEC-72's errors show here).
- **Pre-registered (PM sign-off, verbatim): the statistic** is P(B > A) and P(C > A), pairwise win rates; **the resampling unit** is
  the slot (slot-cluster bootstrap); **2,000 resamples, ties counted ½, n = 18 slots**; each CI is **97.5% (Bonferroni over the two
  primaries)**. **A's line rule** is as above. With 18 clusters the interval is wide, so **a null result may mean too little data**.
  **Decision rule: if neither primary CI excludes 0.5 in B's or C's favour, the wave after the paid run simplifies toward A**
  (SPEC-87's target). P(C > B) is descriptive, with no decision weight. No Bradley-Terry.
- **Amendments, 2026-10-19, before any probe or run data** (pm-w13-plan-signoff.md §1, §5, §7; "§5" is its reworded question).
  - **Slots (N7, a rule):** one ingestion and one fixed outline per manuscript, made in its first dispatch before any arm; slot *k* =
    (manuscript, outline chapter *k*, that chapter's first planned beat), *k* = 1…6; an outline with fewer than 6 chapters stops the
    run before any arm, recorded; `slots.json` is written before arm B of slot 1. Order: Frankenstein, Wuthering Heights, cel.
  > **Generator rule (signed 2026-10-19, before any probe or run data).** Candidates in order: `gemini-3.6-flash` (prod's prose model),
  > `gemini-3.5-flash-lite`, the OpenRouter pin, Groq `openai/gpt-oss-120b`. The generator is the first candidate for which all three
  > of these hold:
  > - (i) `SLOT_WORST ≤ ⌊0.8 × RPD⌋`;
  > - (ii) the dry run's largest single request (prompt + `max_tokens`) ≤ the model's per-minute token cap;
  > - (iii) Σ run-days ≤ 7 for three manuscripts, or ≤ 7 for Frankenstein + Wuthering at n = 12.
  >
  > The panel is the two highest-ranked connected models from a family other than the generator's. Every results line reads
  > `free (<generator id>[, not prod's prose model]), model panel, not human`. If no candidate is feasible, the reworded §5
  > (below) is asked, and nothing is dispatched until the user answers.

  > **Panel (amendment, 2026-10-19, before data).**
  > - Two counted members, chosen by the B1 panel rule. There is no sensitivity member.
  > - Each pair is shown in both orders. A member that is inconsistent across the two orders scores ½.
  > - The pair outcome is the members' agreement. If the members disagree, the pair scores ½.
  > - Continuity is scored per item, as the mean of the two members.
  > - Agreement is Fleiss κ, both overall and per pair, from one function. If κ < 0.2, the result is recorded as **no decision**.
  > - The re-pin rule applies to the OpenRouter member.
  - **n = 12 (PM §7):** If the chosen generator fits Frankenstein + Wuthering in 7 days but not all three manuscripts, cel is dropped
    and n = 12, with wider CIs.
- **Rating is wave 14:** the panel and `score`'s rated path are parked at `w14-panel-parked` (`5350c2f`, pushed before the first
  W12-9 dispatch); wave 13 ends at sealed bundles. Every results line also states that C is the paid-tier ensemble.
- **Amendment, 2026-10-21, non-data-driven** (before any rating; principal panel gate P1): panel refuses empty input; score
  refuses incomplete slot coverage, three pairs per slot (P1). D1's `unrated` refusal (`2446afa`) was pre-approved.
- **Amendment, 2026-10-21, non-data-driven** (before any rating; principal panel `max_tokens` ruling): every panel call sends `max_tokens=640` (Groq gpt-oss 1,664 after the thinking guard), so cel's largest request (6,052 o200k prompt tokens) fits Groq's 8,000 TPM with 284 to spare; both members, all 54 pairs, one condition.
- **Amendment, 2026-10-21, failure handling only** (Codex P2 on PR #52, found in review, not in data): each panel reply is parsed
  before it is memoized, so an unparseable reply is re-asked on resume instead of replayed; valid replies are stored and parsed as
  before under the same keys. The rating continues at P′ = `4c70d0d` (P + this line + `panel.py:94`'s syntax-only rewrite) from the
  same rate-state.
- **Amendment, 2026-10-21, failure-driven, not outcome-driven** (run 39574086903; no Nemotron rating existed): Nemotron reasons
  inline in the reply, so at 640 tokens its first reply was cut off before any JSON, and with room to finish, the reply's first JSON
  object could be the prompt's own quoted example. At P″ = `19838a1` the panel reads each reply's last JSON object, and the OpenRouter
  member sends `max_tokens=4096`; Groq stays at 640, and its 125 cached replies parse identically under the new reader (125/125).
  Each member keeps one condition across all 54 pairs.
- **Amendment, 2026-10-21, failure handling only** (run 39594561205: Nemotron at temperature 0 answered the same prompt with bare
  JSON once and with no parseable answer another time): an unparseable reply is re-asked up to 3 times and, failing that, left
  unrated (D1 counts it; `score` refuses until 0; the next resume re-asks only those); a pair is recorded only when both orders
  parse. P‴ = `c51d49e`. The 128 cached replies parse identically.
- **Amendment, 2026-10-21, failure handling only** (Codex P2 on PR #52): a preference must be the integer 1 or 2 and an error count a
  non-negative integer; anything else is re-asked and, failing that, left unrated. P⁗ = `5dd2d7a`. All 128 cached replies are in
  range (92 preferences, 36 error counts) and read identically.
