# SPEC-68 — Eval preflight and re-login (W9-3)
**Closes**: W9-3 (a)(b)(c), S6, S7, S13 · **Size**: S · **Depends on**: SPEC-22, -54, -58.
- **Re-login (a).** `PolyphonyClient` keeps the eval user's email and password in memory only. On a 401, `_send` logs in
  once and retries once (a second 401 is returned); `_put` goes through `_send`. The report's `relogin` counts the events and
  the scorecard prints `run=<name> relogin=N`. The eval stack returns to the app's 30-minute access token.
- **Preflight (b).** `evals.run --run <name>` calls `evals.preflight.preflight` before the first step; a failure raises
  `PreflightFailed` (a `SystemExit`), so no step runs and no report is written. `python -m evals.preflight --run <name>`
  checks a stack before a draw. In order, with the message:
  - `--steps` is not `RUNS[name]`'s list: "steps X are not <name>'s";
  - `EVAL_RELAY_DIR` unset: "no relay archive: EVAL_RELAY_DIR unset" (every declared run goes through the relay);
  - a fresh run with a file under `EVAL_CACHE_DIR` or the relay's `answered/`: "fresh run, but <dir> is not empty";
  - no eval user (no admin creds): "no eval user: EVAL_ADMIN_EMAIL/PASSWORD unset";
  - `GET /usage` `daily_token_limit` below the minimum: "daily limit N: run needs ≥ M".
  No reachability check and no skip flag. Undeclared runs (e2e, CI) are not preflighted and are never gated.
- **Minimums** are (archived characters / 4) × 1.3, rounded up to 10k: `gated-full` 310,000 from W8r 319.6k minus ensemble
  (`0036`–`0045`) 65.7k minus attribution 22.0k = 231.9k; `gated-scenes` 260,000 from W8c 199.0k; `attribution` 40,000 from
  W8a 23.3k (60 calls). The tier default (200k) fails `gated-full`.
- **Draft path.** `plot draft <dir>` refuses a directory with no `outline:beats` record: "no outline:beats records in <dir>" (W8b).
- **(c)** The `[[eval-ingestion-copy]]` marker is deleted (an upload without a `book_id` gets its own book; hashes are per book).
  The §5.1 deletion row (`eval-ingestion-copy`, rg empty) pins it, replacing the "ingestion bytes" test row (principal ruling).
