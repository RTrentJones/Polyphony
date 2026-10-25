# SPEC-88 — Wave-13 record: counts, cuts, the W13-0 probe, run-W13dry, the stop path
**Closes**: wave-13 §5.3, F5, N5 · **Size**: docs · **Depends on**: SPEC-81, SPEC-84. The W12-9 ledger is wave 14's first commit.
- **Counts of record** (net against `275037c`, §0): product **+2** (F6) **+131** frontend (`55d8aae`, the user-reported Extract control, outside
  the wave's caps), tests **+680**, YAML **+71**, docs **+46** before this file. Test cap 690 → 720 (E2-2) → 660 → **680** (code gate).
- **Cut:** `LLM_ON_QUOTA`, the slot guard, `cancel_job` (now `restore_cancel.sql`), Cohen κ, Gemma, PR-0; the recorder was cut and
  restored (E2-3). **To wave 14:** the N1/N2 rows; the rate job, its leak guard, the stub panel; **W13-4**, at local `w14-panel-parked`
  `5350c2fea32b1fde379f2ad4b83e81e71c5acb4a` (2026-10-19 23:30Z, never pushed or rewritten; this line, pushed before the first W12-9
  dispatch, proves the panel predates the data); `evals/baseline/` blobs `panel.py` `a26e655e31380ab6970576cbc40096af26c86bff`, `score.py`
  `c4635ccf89b2c0909b6068d62bd484e451949ed9`, `blind.py` `a7015ff528c13013e8ba06d2682fd965cea04252`; wave 14 merges them or dates each change in SPEC-84.
- **W13-0 probe** (2026-10-20): pushed `03d3a40286039df2940b2bc55564250608115dcc` (on `dc546f3`), run 39373161278, `reset_utc` 07; reverted by
  `fc09fb850ad7cdf2d062708f19e296cb27657a42` (tree == `dc546f3`), merged here with no tree change. **flash** (`gemini-3.6-flash`): 2 ok, 429 at
  call 3, `quotaId` GenerateRequestsPerDayPerProjectPerModel-FreeTier, `quotaValue` **20**, no lower-bound flag. **Flash-Lite**: 200, one call
  (RPD not measured). **Groq**: 403, Cloudflare `error code: 1010`, no `x-ratelimit-*` headers: unprobed. **OpenRouter**: `/key` `is_free_tier`
  true, limit null, usage 0; pin `nvidia/nemotron-3-ultra-550b-a55b:free` (200). **Cost:** prod lost at most flash's last two calls that day.
- **run-W13dry** (stub, `7ab26f4`; real prompts): R_m Frankenstein 208, Wuthering 215, cel 201; per slot A 3, B 4, C 25–27 (2 rounds
  each time). `SLOT_WORST` = max(34 measured per slot, **58** to start: B 4 + the estimate gate's C 51 + A 3). Largest request ≈ 42.7k
  tokens (Wuthering outline `continuity`). Recorder archives hold no Authorization or bearer string. Two defects, fixed in `773391f`:
  resume lost the restored book (a fresh eval user per dispatch); the contract raised on the baseline archive. **Feasibility** (B1 rule):
  its table lands on the wave-14 branch, pushed before the first W12-9 dispatch (PM §1 condition 1; PM: Flash-Lite conditional, pending AI Studio).
- **Stop path (F5).** Read a red dispatch from `report.json` → `steps.baseline.error` first, then `jobs.error`:

  | Where | What it says | Meaning |
  |---|---|---|
  | `steps.baseline.error` | HTTP 429 at enqueue (the estimate gate) | The quota day is over; resume on the next run-day |
  | `jobs.error` | `DailyRequestLimitError` | The estimate under-predicted; the local ledger stopped the job before a provider call |
  | `jobs.error` | A provider 429 body | `LLM_MAX_RPD` was set above the real RPD; correct it from the body's `quotaValue` |
- **n = 12 (PM §7):** If the chosen generator fits Frankenstein + Wuthering in 7 days but not all three manuscripts, cel is dropped
  and n = 12, with wider CIs.
- **N5.** `sample_lift` and `plan_quotes_source` are not graded over the recorder archive (the contract assumes a gated draw's
  chapter shape: `773391f`); those readings wait for wave 14. W13-SL (ii)'s `voice_overlap --book` sweep is unaffected.


## Amendment 2026-10-20: the W12-9 generator (pushed before the first W12-9 dispatch)
- **Selected: `gemini-3.5-flash-lite`** by the signed B1 rule. `max_rpd` **800**; `LLM_MODEL` = `LLM_MODEL_FAST` = it; label `free
  (gemini-3.5-flash-lite, not prod's prose model), model panel, not human`. (i) 800 ≥ 58; (iii) one run-day per manuscript.
  **RPD 1,000** is the registry's `fast_max_rpd` (admitted, PM §1 condition 1), the weakest source: the same registry's flash 250 was
  12.5× the measured 20, so the first dispatch's per-day `quotaValue`, if any, replaces it. **(ii) measured** by the W13-1 probe
  (pushed `bc22ddc99ded3e518e29cf6061b1a2ac3d07512f`, run 39388305450, reverted by `64c225cdd6cb6e7ed86f1caa8a9f4bcad65d4ef4`):
  52,372 prompt tokens accepted twice 7 s apart, so the per-minute cap is ≥ 104.7k > 42.7k.
- **Not selected:** `gemini-3.6-flash` (RPD 20, measured: fails (i), (iii)); Groq `gpt-oss-120b` (8K TPM published: fails (ii)); the
  OpenRouter pin was not reached.
- **Operator limits** (principal sweep L1, L2): a failed outline memoized under `outline` makes every resume of that state stop at
  prep; dispatch fresh (no `prior_run`). A resume with another `sha` reuses no memo (quota spent, never mixed): pass the same `sha`.
- **Tests:** the S1/S2 rows landed in PR-A with the user's authorisation; tests **+738** against the 720 backstop.
- **Amended 2026-10-20 21:00Z: Flash-Lite RPD is 500, measured.** Cel's run 39495277558 drew a provider 429 with `quotaId`
  GenerateRequestsPerDayPerProjectPerModel-FreeTier, `quotaValue` **500** (stop-path row 3: `LLM_MAX_RPD` above the real RPD). It
  replaces the registry's 1,000 (2× high). `max_rpd` becomes **400** for every later dispatch; (i) 400 ≥ 58 and (iii) one run-day per
  manuscript still hold, so the selection stands. Frankenstein and Wuthering finished under 800 without reaching it (cap, not content).

## W12-9 dispatch ledger (wave 14, W14-0)
All runs `baseline.yml` at app sha `e428c77`; generator `gemini-3.5-flash-lite` unless noted. Artifacts expire 2026-11-19 (W14-6).

| Run | Manuscript | Prior | `max_rpd` | Result | Sealed artifacts (id, sha256 of the zip) |
|---|---|---|---|---|---|
| 39389623526 | cel demo (stub) | — | 100 | red: 2/6, estimate-gate 429 (row 1) | — |
| 39462843795 | cel demo (stub) | 39389623526 | 800 | green 6/6 (A 18, B 24, C 78) | bundle 12283578300, key 12283912980 |
| 39466797409 | frankenstein | — | 800 | red: 4/6, slot 5 arm B Gemini 503 (first 503) | — |
| 39475808924 | frankenstein | 39466797409 | 800 | green 6/6 | bundle 12288159864 `f841cbe9…de24`; key 12291070021 `a96c1609…76e5` |
| 39480425231 | wuthering | — | 800 | green 6/6, one pass | bundle 12294107221 `029e3a1f…5df3`; key 12293832683 `04ddc599…c7ff` |
| 39487847529 | cel | — | 800 | red: outline "no parseable JSON array" (L1: dispatch fresh) | — |
| 39495277558 | cel | — | 800 | red: 2/6, provider 429 `quotaValue` 500 (row 3) | — |

Full digests and state-artifact ids are in the orchestrator's ledger; keys and records stay unopened until W14-5.

**Rate dispatches** (W14-5; workflow blob `6d26176` throughout). 39574086903 · `sha=P` (`9312365`) · no prior · red after
2h05m: Groq 125 replies, then stopped cleanly at its 200K tokens-per-day limit; Nemotron's first reply was reasoning cut off at 640
tokens (ValueError), memoized at P; no kappa.json · rate-state 12341190753, 126 replies, 1 invalid (`panel-4c92d396408f6e102cf0`).
39594561205 · `sha=P″` (`19838a1`) · prior 39594493758 (the rate-state re-uploaded without that reply) · red: Groq 1 reply then its
tokens-per-day stop; Nemotron 2 valid bare-JSON replies, then one unparseable (ValueError) · rate-state 12343242346, 128 replies, 0
invalid. From P‴ (`c51d49e`) on, one row each, plus Nemotron requests spent, replies cached and re-asks:
39598043470 · `sha=P‴` (`c51d49e`) · prior 39594561205 · green, a clean stop on both members: Groq 0 replies (tokens-per-day,
195,445/200K); Nemotron 46 requests, 39 replies cached, 4 re-asks that then parsed, 1 prompt left unrated after 3
(`panel-9d1545248cbffb2ce2dd`, a nested `{"errors": {"count": 0, …}` missing its closing brace), then OpenRouter's 50/day ·
rate-state 12347567923: 167 checked against P⁗'s range rule, 0 removed · `unrated` 85 · f = 7/46 ≈ 0.15, Nemotron 121 prompts
left: ⌈121 × 1.3 / 50⌉ = 4 days (about 10-25). Next resumes check out P⁗ (`5dd2d7a`).
39698076332 · `sha=P⁗` (`5dd2d7a`) · prior 39598043470 · green, a clean stop on both members: Groq 23 replies, then its
tokens-per-day stop (13 prompts left); Nemotron 51 requests, 29 replies cached (yesterday's unrated prompt among them), 2 prompts
left unrated after 3 (`panel-c4e91c81004ae9a06d1c`, `panel-a2bb6330b737efba623f`: a nested `{"errors": {"count": …}}`, the shape
the prompt does not ask for), then OpenRouter's 50/day · rate-state 12395515272: 219 checked, 0 removed · `unrated` 65 · f ≈
22/51 ≈ 0.43, Nemotron 92 prompts left: ⌈92 × 1.86 / 50⌉ = 4 days (about 10-26), inside 11-10.
39876568769 · `sha=P⁗` · prior 39698076332 · green, a clean stop: Groq 13 replies, its last prompts (Groq complete, 162/162);
Nemotron 51 requests, 25 replies cached (both prompts unrated on day 2 among them), 5 prompts left unrated after 3 (three
16k-character answers cut off at 4,096 tokens, one `{}`, one nested `{"errors": {…}}`), then OpenRouter's 50/day · rate-state
12471989243: 257 checked, 0 removed · `unrated` 49 · f ≈ 26/51 ≈ 0.51, Nemotron 67 prompts left: ⌈67 × 2.02 / 50⌉ = 3 days
(about 10-26), inside 11-10.
40043768197 · `sha=P⁗` · prior 39876568769 · green, a clean stop: Groq complete (0 requests); Nemotron 55 requests (4 × 429),
about 30 replies cached, 7 prompts left unrated after 3, all 16k-character reasoning cut off at 4,096 tokens
(`panel-2366b0205399db689284`, `panel-e64d16605cc3c761a023`, `panel-1ad0cb3a1cc4d21a8610` for the second run, which goes to the
principal; `panel-ba5e1538299e93415c89`, `panel-3127fb157de5b406aef3`, `panel-cc97d25b80c727066774`, `panel-601863f116ba3708685a`),
then OpenRouter's 50/day · rate-state 12541928872: 280 checked, 0 removed · `unrated` 35 · the principal's ruling: P⁵ (SPEC-84,
2026-10-24); about 48 Nemotron prompts left at ~1.3 requests each: 2 days (about 10-26). Next resumes check out P⁵ (`f57f026`).
40145592398 · `sha=P⁵` (`f57f026`) · prior 40043768197 · green, a clean stop: Nemotron 55 requests, the day-4 cut-off prompts
answered within 16384 tokens except `panel-cc97d25b80c727066774` (3 replies of `{}`: its first P⁵ failure; SPEC-84's fallback needs a
second), then OpenRouter's 50/day · rate-state: 319 checked, 0 removed, 319/319 read identically at P⁵ · `unrated` 5 · 1 day left
(about 10-26).
 run id · `sha=19838a1` (P + the recorded `panel.py` lines) · `prior_run` · rate-state check (invalid
entries removed, by key, or none) · `unrated` after the dispatch.

## Panel feasibility record (wave 14, before the first rating call)
- **P** = `931236534a962414c31944ab6049c99bf35d885d` (`app/` identical to `e428c77`); `baseline.yml` blob `6d26176b25aa9a57f1b44f13b0a8a06a732aa43b`.
  Every rate dispatch runs `sha=P`; `baseline.yml` and `evals/baseline/` stay frozen at P until `unrated` = 0. Tags are refused by
  the remote, so P is named by sha. The stub rate demo at P (run 39567897491, bundle 39462843795) was green: `unrated` 0, and score
  read it end to end.
- **Panel:** `groq:openai/gpt-oss-120b,openrouter:nvidia/nemotron-3-ultra-550b-a55b:free`, two vendors and families, both priced at
  `e428c77` (P2). Groq re-probe through the openai SDK (run 39546316560, reverted `91fb912`): 200; `x-ratelimit` 1000 RPD,
  8000 TPM. The wave-13 403/1010 was the probe's urllib client. Groq TPD **200K** (console.groq.com/docs/rate-limits, read
  2026-10-21, undated). OpenRouter `/api/v1/key` (run 39568324733, reverted `a9f0696`): `free_model_daily_requests` limit **50**.
- **Load at P** (o200k_base over the real `panel.rate` prompts; every call `max_tokens` 640, Groq sent 1,664 after the thinking guard):
  largest request L = Frankenstein 3,151, Wuthering 4,055, cel 7,716 (≤ 8,000 TPM, test (ii) holds); `LLM_MAX_RPM` 1.
  Input 427,296 tokens per member over 162 requests (54 per manuscript).
- **Days** = ⌈(Σ prompt + Σ completion) / TPD⌉ for Groq ≈ 3 (≤ 4 at the 1,664 output bound); Nemotron ⌈162 / 50⌉ = 4. Rating ends
  before the 2026-11-14 re-upload line (W14-6). The completion total is replaced by the first dispatch's `api_usage` sum.
