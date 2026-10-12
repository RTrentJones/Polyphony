import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EstimateHint from '@/components/EstimateHint'
import type { Estimate } from '@/lib/usage-api'

const { estimate } = vi.hoisted(() => ({ estimate: vi.fn() }))
vi.mock('@/lib/usage-api', async (orig) => ({
  ...(await orig<typeof import('@/lib/usage-api')>()),
  estimate,
}))

const base: Estimate = {
  action: 'scene',
  mode: 'prose',
  calls: 6,
  input_tokens: 25_000,
  output_tokens: 6_000,
  tokens: 31_000,
  usd: 0,
  degraded_from: null,
  notes: [],
  remaining_tokens_24h: 200_000,
  remaining_requests_today: 240,
  share_of_remaining: 0.155,
  allowed: true,
  reason: null,
}

describe('EstimateHint (SPEC-12)', () => {
  beforeEach(() => estimate.mockReset())

  it('shows calls, tokens and share of the budget', async () => {
    estimate.mockResolvedValue(base)
    const onEstimate = vi.fn()
    render(
      <EstimateHint
        request={{ action: 'scene', book_id: 'b' }}
        onEstimate={onEstimate}
        debounceMs={0}
      />
    )
    expect(await screen.findByText(/≈ 6 calls · 31k tokens · 16% of today's budget/)).toBeTruthy()
    await waitFor(() => expect(onEstimate).toHaveBeenCalledWith(base))
  })

  it('explains a refusal', async () => {
    estimate.mockResolvedValue({ ...base, allowed: false, reason: 'Needs about 31,000 tokens' })
    render(<EstimateHint request={{ action: 'outline', book_id: 'b' }} debounceMs={0} />)
    expect((await screen.findByTestId('estimate-reason')).textContent).toMatch(/31,000/)
  })

  it('says when the tier runs a different mode', async () => {
    estimate.mockResolvedValue({
      ...base,
      degraded_from: 'ensemble',
      notes: ['ensemble is not available on the free tier; prose mode will run.'],
    })
    render(<EstimateHint request={{ action: 'scene', book_id: 'b', mode: 'ensemble' }} debounceMs={0} />)
    expect(await screen.findByText(/runs as prose/)).toBeTruthy()
    expect(screen.getByText(/not available on the free tier/)).toBeTruthy()
  })

})
