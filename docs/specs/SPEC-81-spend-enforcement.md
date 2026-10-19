# SPEC-81 — Spend enforcement: one price table, an exact monthly ceiling, named pauses
**Closes**: W12-1 (with the PM's B3/B5 sign-off and the E1 checkpoint ruling) · **Size**: S · **Depends on**: SPEC-03, -07, -08.
- **Prices.** `PRICES`, keyed by the *resolved* model, is the one price source; no row = unpriced (`cost_usd` `None`; an estimate
  says "price unknown", never $0). No row carries a date on which nobody read its source: gpt-4o's 2.5 / 10 is carried from
  `Provider.rate_in` @ e954eae, and it and the free/stub rows (Gemini: if unbilled, U6) are "unverified"; W12-9 re-prices from the
  vendor's page that day. gpt-4o-mini, grok-4 have no row: under a ceiling with `LLM_PROVIDER=openai` every fast call refuses (B3).
- **Admission (B1, B2).** Only with `MONTHLY_COST_CEILING_USD` set (`0` = unset), before every call. Worst case = `prompt_chars // 4`
  in + the sent cap out. A free model never reads the ledger; an unreadable ledger pauses (`accounting_unavailable`). Else one
  `asyncio.Lock` covers reading the live `deployment_cost_30d`, checking it + `_inflight` + worst case, and holding the worst case,
  released once its row commits or when no response came (a failed record keeps it). Exact for the worker + concurrent probes, except a
  call sent and then abandoned without a response — a crash, a probe's 504 (`wait_for` cancels it), or a timed-out attempt the retry
  loop resends — whose cost goes unrecorded: overshoot ≤ one worst case per such call, + the `// 4` error. A cancellation between
  response and release keeps its hold until restart (conservative). **Process-local: a 2nd replica needs the DB form.** N6: a row per
  response, with `model` and `job_id`; `/usage` adds `unpriced_tokens_30d`.
- **AC (d), B3 (verbatim):** When `MONTHLY_COST_CEILING_USD` is set, a call to a model with no `PRICES` row raises `UnpricedModelError`.
  The call is logged as `llm_unpriced_refused`, and **the job fails**. The failure message names the model and says the fix is a
  `PRICES` row with a source and a date. When no ceiling is set, cost is still recorded, as `None` if the model is unpriced. Free, stub
  and relay models carry explicit `0` rows. Known: scenes turn it into a failed scene; `dialogue.py` swallows it, as it does quota.
- **AC (c), B5 (verbatim):** past the ceiling, queued work **pauses, up to the 48 h pause bound, and then fails**. `SpendPausedError`
  pauses on every tier (`unhinted_pause_seconds`); `jobs.pause_seconds[reason]` adds each scheduled pause. At the bound: `monthly
  ceiling reached; job stopped after 48h paused; re-running starts this job over` (or `accounting unavailable; …`; quota keeps its
  text). **A re-run starts over** (going dead clears the checkpoint; U4). The probe answers 429 on a spend pause, 503 when unpriced.
