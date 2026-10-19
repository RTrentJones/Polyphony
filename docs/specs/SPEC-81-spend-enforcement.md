# SPEC-81 — Spend enforcement: one price table, an exact monthly ceiling, named pauses
**Closes**: W12-1 (as amended by the PM's B3/B5 sign-off) · **Size**: S · **Depends on**: SPEC-03, -07, -08.
- **Prices.** `app/llm/prices.py` `PRICES`, keyed by the *resolved* model, is the one price source: `Price(in_per_m, out_per_m, source,
  date)`. No row means unpriced: `cost_usd` is `None`, and `api_usage.cost_usd IS NULL` is the one marker. gpt-4o is 2.5 / 10; free
  vendors, the stub and the relay have explicit `0` rows (Gemini only on an unbilled project, U6). gpt-4o-mini and grok-4 have no row.
  **One predicate (B2):** with `MONTHLY_COST_CEILING_USD` set, `spend.admit` runs before every call; else nothing is enforced.
- **Admission (B1).** Worst case = `prompt_chars // 4` in (the `_check_user_budget` estimate) + the sent cap out. A free model returns
  at once and never reads the ledger. Otherwise one `asyncio.Lock` covers reading the live `deployment_cost_30d` (never the 30 s cache),
  checking it + `_inflight` + worst case against the ceiling, and holding the worst case. The hold is released after the usage row
  commits or when no response came; a failed record keeps it. Exact for the worker plus concurrent voice probes; the overshoot bound is
  one in-flight worst case on a crash, plus the `// 4` estimate error. **Process-local: a second replica needs the DB form.**
- **Usage rows (N6):** `model` and `job_id` on each; written whenever `admit` ran. `GET /usage` adds `unpriced_tokens_30d`.
- **AC (d), B3 (verbatim):** When `MONTHLY_COST_CEILING_USD` is set, a call to a model with no `PRICES` row raises `UnpricedModelError`.
  The call is logged as `llm_unpriced_refused`, and **the job fails**. The failure message names the model and says the fix is a
  `PRICES` row with a source and a date. When no ceiling is set, cost is still recorded, as `None` if the model is unpriced. Free, stub
  and relay models carry explicit `0` rows. An unreadable ledger pauses a priced call (`accounting_unavailable`); the budget stays open.
- **AC (c), B5 (verbatim):** past the ceiling, queued work **pauses, up to the 48 h pause bound, and then fails**. `SpendPausedError`
  pauses on every tier (backoff: `unhinted_pause_seconds`); `jobs.pause_seconds[reason]` adds each scheduled pause. At the bound:
  `monthly ceiling reached; job stopped after 48h paused; re-running starts this job over` (or `accounting unavailable; …`; quota
  keeps its text). **A re-run starts over**: going dead clears the checkpoint, and a re-run is a new job (U4). The probe answers 429.
