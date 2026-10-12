import { describe, expect, it } from 'vitest'

import {
  MAX_BACKOFF_MS,
  backoffDelayMs,
  isRetryable429,
  parseRetryAfter,
  rateLimitMessage,
  rateLimitedPollDelay,
} from '@/lib/rate-limit'

describe('rate-limit helpers (SPEC-05)', () => {
  it('parses Retry-After seconds and HTTP dates', () => {
    expect(parseRetryAfter('7')).toBe(7000)
    const now = Date.parse('2026-10-12T00:00:00Z')
    expect(parseRetryAfter('Mon, 12 Oct 2026 00:00:10 GMT', now)).toBe(10_000)
    expect(parseRetryAfter('')).toBeNull()
    expect(parseRetryAfter('soon')).toBeNull()
  })

  it('backs off exponentially, honours Retry-After, and caps', () => {
    expect(backoffDelayMs(0, 2000)).toBe(2000)
    expect(backoffDelayMs(3, 1000)).toBe(8000)
    expect(backoffDelayMs(0, 3_600_000)).toBe(MAX_BACKOFF_MS)
  })

  it('retries only reads', () => {
    expect(isRetryable429(429, 'get')).toBe(true)
    expect(isRetryable429(429, 'post')).toBe(false)
    expect(isRetryable429(500, 'get')).toBe(false)
  })

  it('tells the user when to try again', () => {
    expect(rateLimitMessage(1000)).toMatch(/1 second\./)
    expect(rateLimitMessage(null)).toMatch(/wait a moment/)
  })

  it('slows a polling loop only on 429', () => {
    expect(rateLimitedPollDelay({ status: 429 })).toBe(MAX_BACKOFF_MS)
    expect(rateLimitedPollDelay({ status: 500 })).toBeNull()
    expect(rateLimitedPollDelay(null)).toBeNull()
  })
})
