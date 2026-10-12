/**
 * Backing off on HTTP 429 (SPEC-05).
 *
 * The backend rate-limits per user: polling reads get a generous bucket, LLM
 * generation a tight one, and every 429 carries `Retry-After`. Polling loops
 * must slow down when told to instead of hammering the limit, so the API
 * client retries idempotent GETs after the server's suggested wait (with
 * exponential growth and a cap), and surfaces a readable message otherwise.
 */

/** Retries of one GET on 429 before the error reaches the caller. */
export const MAX_429_RETRIES = 3

/** Never sleep longer than this on one 429 (an hourly bucket can say 3600s). */
export const MAX_BACKOFF_MS = 60_000

const DEFAULT_RETRY_AFTER_MS = 5_000

/** Parse a `Retry-After` value (delta-seconds or HTTP-date) into milliseconds. */
export function parseRetryAfter(value: unknown, now: number = Date.now()): number | null {
  if (value === undefined || value === null || value === '') return null
  const text = String(value).trim()
  const seconds = Number(text)
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000)
  const date = Date.parse(text)
  if (!Number.isNaN(date)) return Math.max(0, date - now)
  return null
}

/**
 * How long to wait before retry number `attempt` (0-based): the server's
 * Retry-After when given, never less than an exponential floor, capped.
 */
export function backoffDelayMs(attempt: number, retryAfterMs: number | null): number {
  const floor = 1_000 * 2 ** attempt
  const wanted = Math.max(retryAfterMs ?? DEFAULT_RETRY_AFTER_MS, floor)
  return Math.min(wanted, MAX_BACKOFF_MS)
}

/** Only reads are retried transparently: a POST that enqueues generation is not. */
export function isRetryable429(status: number | undefined, method: string | undefined): boolean {
  return status === 429 && (method ?? 'get').toLowerCase() === 'get'
}

/** User-facing text for a 429 that was not (or could no longer be) retried. */
export function rateLimitMessage(retryAfterMs: number | null): string {
  if (retryAfterMs === null) return 'Too many requests. Please wait a moment and try again.'
  const s = Math.max(1, Math.ceil(retryAfterMs / 1000))
  return `Too many requests. Please try again in ${s} second${s === 1 ? '' : 's'}.`
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * For a polling loop's catch: if the poll failed on a 429 the client already
 * gave up retrying, wait the long way round before the next tick (returns the
 * delay); for any other error returns null and the loop handles it as before.
 */
export function rateLimitedPollDelay(err: unknown): number | null {
  const status = (err as { status?: number } | null)?.status
  return status === 429 ? MAX_BACKOFF_MS : null
}
