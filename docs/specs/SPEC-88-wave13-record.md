# SPEC-88 — Wave-13 record: counts, cuts, the W13-0 probe, run-W13dry, the stop path
**Closes**: wave-13 §5.3, F5, N5 · **Size**: docs · **Depends on**: SPEC-81, SPEC-84. The W12-9 ledger is wave 14's first commit.
- **Counts of record** (net against `275037c`, §0): product **+0** (F6 adds ≤ 3), tests **+680**, YAML **+71**, docs **+46** before this
  file. Test cap: 690 → 720 at E2-2 (= backstop) → 660, W13-4 deferred → **680** at the code gate; the backstop stayed at 720.
- **Cut:** `LLM_ON_QUOTA`, the slot guard, `cancel_job` (now `restore_cancel.sql`), Cohen κ, Gemma, PR-0; the recorder was cut and
  restored (E2-3). **To wave 14:** the N1/N2 rows; the rate job, its leak guard, the stub panel; **W13-4**, at local `w14-panel-parked`
  `5350c2fea32b1fde379f2ad4b83e81e71c5acb4a` (tree `07cea9ac3a13007223f4f5ad93a1896cd1cf833a`, 2026-10-19 23:30Z, never pushed or
  rewritten; this line, pushed before the first W12-9 dispatch, proves the panel predates the data), blobs `evals/baseline/panel.py`
  `a26e655e31380ab6970576cbc40096af26c86bff`, `score.py` `c4635ccf89b2c0909b6068d62bd484e451949ed9`, `blind.py`
  `a7015ff528c13013e8ba06d2682fd965cea04252`. Wave 14 merges these blobs, or each difference is a dated SPEC-84 amendment.
- **W13-0 probe** (2026-10-20): pushed `03d3a40286039df2940b2bc55564250608115dcc` (on `dc546f3`, unmerged branch), run 39373161278,
  `reset_utc` 07; reverted by `fc09fb850ad7cdf2d062708f19e296cb27657a42` (tree == `dc546f3`), merged here with no tree change.
  **flash** (`gemini-3.6-flash`): 2 ok, 429 at call 3, `quotaId` GenerateRequestsPerDayPerProjectPerModel-FreeTier, `quotaValue`
  **20**, no lower-bound flag. **Flash-Lite**: 200, one call (RPD not measured). **Groq**: 403, Cloudflare `error code: 1010`, no
  `x-ratelimit-*` headers: unprobed. **OpenRouter**: `/key` `is_free_tier` true, limit null, usage 0; pin
  `nvidia/nemotron-3-ultra-550b-a55b:free` (200). **Cost:** on prod's key, bounded (signed) by its last two Pacific hours; spent 2
  flash calls, the day's last two (429 at call 3, retry 6755 s to the 07:00Z reset), so prod lost at most those two.
- **run-W13dry** (stub, `7ab26f4`; real prompts): R_m Frankenstein 208, Wuthering 215, cel 201; per slot A 3, B 4, C 25–27 (2 rounds
  each time). `SLOT_WORST` = max(34 measured per slot, **58** to start: B 4 + the estimate gate's C 51 + A 3). Largest request ≈ 42.7k
  tokens (Wuthering outline `continuity`). Recorder archives hold no Authorization or bearer string. Two defects, fixed in `773391f`:
  resume lost the restored book (a fresh eval user each dispatch); the prompt contract raised on the baseline archive.
  **Feasibility** (B1 rule): its table lands on the wave-14 branch, pushed before the first W12-9 dispatch (PM §1 condition 1).
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
