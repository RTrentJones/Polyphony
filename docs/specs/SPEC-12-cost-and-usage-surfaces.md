# SPEC-12 — Cost before action, and usage surfaces

**Closes**: F15 (R7.5, `/usage` has no page), F17 (silent ensemble downgrade) · **Size**: M ·
**Depends on**: SPEC-03, SPEC-07

## Why

R7.5 asks that cost be shown before an expensive action, and nothing does it. The only preflight
is the staged outline's private estimate, which silently decides between staged and single-call.
`/usage` reports the tier, the rolling budget and spend, but no page renders it. When ensemble is
requested on the free tier, the request is quietly downgraded to prose and the response does not
say so.

## Outcome

Every LLM-spending button shows an estimate (calls, tokens, share of today's remaining budget, and
dollars on a paid tier) before the author commits, and the response says which mode actually ran.
A Usage page shows today's budget, requests against the provider's daily ceiling, recent spend by
purpose, and any paused jobs.

## Scope

**In**: an estimate endpoint, estimate display on the generate buttons, response mode reporting,
a Usage page, navbar budget indicator.
**Out**: per-user tiers and billing.

## Design

1. **Estimators.** One module, `app/core/estimates.py`, owns per-action estimators
   (`scene(mode, beats, cast, canon)`, `outline(staged|single, canon)`, `extraction(source)`,
   `continuity(scope)`), each returning `{calls, input_tokens, output_tokens, usd, mode,
   degraded_from?}`. The enqueue endpoints call the same estimator for their preflight so the
   number shown is the number enforced.
2. **Endpoint.** `POST /api/v1/estimate` with `{action, params}` returns the estimate plus
   `remaining_tokens_24h`, `remaining_requests_today` and `allowed: bool` with a reason.
3. **Mode reporting.** Scene and outline enqueue responses include `mode_requested`, `mode_used`
   and `reason` (for example "ensemble is not available on the free tier").
4. **UI.** The generate buttons (scene, outline, extraction, continuity) fetch an estimate when the
   form changes (debounced) and render it beneath the button, e.g. "≈ 6 calls · 31k tokens · 16%
   of today's budget". A disabled button explains why. The ensemble toggle appears in the scene
   modal, disabled with an explanation on the free tier.
5. **Usage page.** `/usage` in the nav: today's tokens against the cap, requests against the
   provider RPD (from SPEC-03's counter), a 30-day spend chart by purpose (from `api_usage.endpoint`),
   and a list of paused or queued jobs with resume times and Cancel (SPEC-07).

## Acceptance

1. The estimate for a staged outline on the `cel` fixture is within ±25% of the tokens actually
   recorded in `api_usage` for a stubbed run whose stub reports realistic usage. *(unit)*
2. An enqueue whose estimate exceeds the remaining budget returns 429 with the estimate in the body,
   and the estimate endpoint returns `allowed: false` for the same input. *(unit)*
3. A free-tier ensemble request returns `mode_used: "prose"` with a reason. *(unit)*
4. The Usage page renders with zero usage and with a paused job. *(frontend component test once
   SPEC-15's harness exists)*

## Docs

BRD R7.5 marked satisfied with a pointer to `estimates.py`; feature set §8 rows updated.
