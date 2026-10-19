# SPEC-87 — Wave-12 gate record: scope, deploy gate, scorecard, run evidence (folds SPEC-82, -86)
**Closes**: wave-12 §6 · **Size**: docs · **Depends on**: SPEC-81, -83, -85, the SPEC-26 amendment; SPEC-84 is wave 13's.
- **Wave.** Closed: W12-1, -2, -3, -4, -6 (a), -7. To wave 13: **W12-5** (lead, measured ≈ 455, `w13-baseline-parked`), **W12-6 (b)**
  (κ, raters). Not started: W12-8, -10. **W12-9** (paid) awaits user resources; its questions went out in wave 12 (PM condition).
  [USER]: each resolved model (default, fast, overrides) gets a vendor-read `PRICES` row that day; U1 (ceiling), U4 (48 h; re-runs start
  over), U6 (Gemini billing). `RUNS["baseline-dev"]` stays ungated to wave 13. Pre-registration of record to SPEC-84:
  `pm-w12-plan-signoff.md` B9/N3, `pm-backlog-wave12.md`'s decision rule, **2,000 resamples**, target **"the wave after the paid run"**.
- **Deploy gate (was SPEC-82).** `greenlight-build.yml`'s `verify` calls `test.yml` (`workflow_call`, `contents: read`); `build: needs:
  [verify]` at the caller's sha. **No aggregator**: a `needs`-only job is *skipped* on failure, and a skipped required check passes.
  Push and notify: `main` only (P5); `RUN_EMBED_TESTS` on the 3.12 leg. **Demo.** Red, run 39300017204 on 5607a66: `verify / Frontend`
  failed, the other four passed, `build` **skipped**; green, run 39301524162 on 2e33686: all five and `build` succeeded, no push
  (non-main), "Notify wrapper to deploy" skipped. The demo commits are on the session's working branch and net to zero; there is no demo
  branch to delete. **U2** (five check names) is unconfirmed; **N8** waits: `main` stays in `test.yml`'s push trigger (double run).
- **Scorecard (was SPEC-86; D7 as signed).** Cost per kept scene; queue wait over `pause_count = 0 AND attempts = 1`, excluded printed;
  pause duration per job from scheduled lengths (after `available_at` is queue wait); acceptance = revise-proposal rate, "edited at
  least once", normalized word edit distance. SQL: `QUERIES[<figure>]`, `evals/ops_scorecard.py`; no `--rows`; `--user` filters. Reason
  strings and `0.2` are copied, not imported; a change to either edits it too.
- **Grounding failures (prose and revise only).** A present principal (`context only` excluded) whose entries for a beat include a
  reason (`no indexed voice samples`, `retrieval exhausted (200-row bound)`) or whose every score is < 0.2, ÷ distinct (scene, beat,
  character_id). Ensemble scenes record no reasons (E1 deviation 5), count only via no-samples or low scores, and are understated.
- **Run evidence** (`dev (relay)`, smoke only; `MONTHLY_COST_CEILING_USD=1` on app and harness). **run-W12** (a039ea2): the harness
  judge's default id `judge` was refused (`llm_unpriced_refused`; W12-1 failing loud, in the harness, no product job); outline, prose and
  revise ungraded. F6: default `stub-default` (priced free), (F) row. **run-W12b** (1c17758, of record): 20 jobs, 0 pauses, 0 refusals,
  76 usage rows priced $0; every §6.2 row but `sample_lift` 16/17 (0038: a 9-word run of a sample its beat scripts; same at e954eae),
  a PM-named exception; `exercised` rc 0 (n 13); `replayed` 0. Regrade: only extraction moved (W8, W11). Round trip byte-identical but
  W12-4's headings; SPEC-83 empty. W12-6 (a): 18/30, Wilson [0.42, 0.75]; Milo 8/10, Edric 8/10, Zara 2/10; no Edric 10/20, chance.
- **PM rulings:** `sample_lift` exception and W13-SL P0, see pm-w12-eval-signoff.md.
- **Corrections.** W12-4's rows are in `test_extraction_scale.py` (not the matrix's `test_extraction_merge.py`); W12-5's registration
  edits are reverted. §5.1: `restates(` in `context.py` reads 2, unchanged from e954eae.
- **Budgets (net, §0).** Product 165 / +150 (accepted at E1: spend 45, errors 15 vs 30, 5 planned); tests 445 / +430 (day-0's PG round
  trip, E1's plan arithmetic, F1, F6); docs 89 / +110; YAML −27. Wave-13 rule: harness work is sized from a measured floor.
- **This gate:** F1 row-6 lock race; F2 abandoned-call cost; F3 no-Edric is chance; F4 (F) determiner; F5 `--topic`; F6 relay judge
  priced; eval B2: `exercised` takes one gated draw (SPEC-75).
