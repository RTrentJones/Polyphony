# SPEC-03 — Quota-safe LLM client and a tier that owns its limits

**Closes**: F3, F4 · **Size**: M · **Depends on**: —

## Why

Two guarantees in the BRD do not hold.

R7.2 (quota pauses, never fails) is defeated by the circuit breaker: it is built with
`expected_exception=Exception` (`app/llm/client.py:71`), so `QuotaExhaustedError` counts toward
its threshold of four, after which calls raise `CircuitBreakerError`, which the worker treats as an
ordinary failure. The breaker is process-global, so one user's quota storm blocks everyone.

R7.6 (paid is one flip) is defeated by the tier object being mostly decorative: `Tier.max_rpm` is
unused, `daily_token_limit` is overwritten from `USER_DAILY_TOKEN_LIMIT`, budget code reads the
setting directly, and the monthly ceiling is display-only. Nothing models the provider's
requests-per-day ceiling, which BRD §6.1 calls the probable binding limit.

## Outcome

Quota exhaustion never trips the breaker and never kills a job on the free tier. One setting,
`LLM_TIER`, determines pacing, token caps, request caps and feature gates, with explicit
per-setting overrides that are documented as overrides. The provider's daily request ceiling is
tracked and respected before a call is made.

## Scope

**In**: `app/llm/client.py`, `app/llm/pacing.py`, `app/llm/tier.py`, `app/core/budget.py`,
`app/core/config.py`, the worker's exception classification.
**Out**: per-user tiers (all users share the deployment tier for now); billing.

## Design

1. **Breaker classification.** Give `CircuitBreaker` an `ignore_exceptions` tuple (or construct it
   with an `expected_exception` that excludes quota). Quota errors and 4xx client errors other than
   429 (bad request, safety block) do not count; connection errors, timeouts and 5xx do. Add
   `CircuitBreakerError` to the worker's pause path: an open breaker pauses the job until
   `recovery_timeout` instead of failing it.
2. **Tier owns limits.** `get_tier()` returns the base tier with overrides applied only when the
   override setting is explicitly set (`USER_DAILY_TOKEN_LIMIT`, `LLM_MAX_RPM` become
   `Optional[int] = None`). `budget.py` and `pacing.py` read `get_tier()` exclusively. Remove the
   duplicate defaults.
3. **Requests-per-day accounting.** Add `Tier.max_rpd` (provider-level, deployment-wide) and a
   small `llm_requests` daily counter (a row per provider per UTC day, incremented in
   `_account` and on every attempt that reaches the provider). Before a call, if the counter is at
   the ceiling, raise `QuotaExhaustedError(reset_after=seconds_until_provider_reset)` without calling
   the provider. Verify the current Gemini free-tier RPD for `gemini-2.5-flash` and
   `gemini-2.5-flash-lite` and record the figures with a date in BRD §6.
4. **Monthly ceiling enforced.** When `monthly_cost_ceiling_usd` is set, `check_user_budget` and the
   preflight helpers also refuse when the deployment's trailing-30-day `api_usage.cost_usd` sum
   would exceed it.
5. **Fast-fail on paid.** On the paid tier, the per-call retry loop honours `retry-after` but caps
   total wait at a configurable `LLM_MAX_RETRY_WAIT_SECONDS` (default 60) so a request-path call
   cannot exceed the proxy timeout.
6. **Model guard.** Refuse a Gemini `LLM_MODEL` override that is a thinking model without
   `reasoning_effort` support, or raise `max_tokens` accordingly (eval-audit backlog item 6).

## Acceptance

1. Ten consecutive stubbed 429s across ten different jobs leave all ten jobs `queued` with a future
   `available_at`, none `dead`, and the breaker `closed`. *(unit, `test_job_worker.py` pattern)*
2. Five consecutive connection errors open the breaker; the next job pauses until the recovery
   window and then runs. *(unit)*
3. Setting only `LLM_TIER=paid` changes the pacer's RPM, the daily token cap and `allow_ensemble`.
   *(unit over `get_tier`, `get_pacer`, `remaining_budget_24h`)*
4. With `max_rpd=3`, a fourth call raises `QuotaExhaustedError` without reaching the provider stub,
   and `reset_after` is the seconds to the provider's next daily reset (midnight Pacific for Gemini). *(unit)*
5. With a $1 monthly ceiling and $1.01 of trailing spend, `check_user_budget` raises 429 with a
   message naming the ceiling. *(unit)*

## Docs

BRD R7.2 and R7.6 gain the precise mechanism; BRD §6 records the verified RPD figures and date.
README "The LLM backend is fungible" lists `LLM_TIER` first. Feature set §8 rows updated.
