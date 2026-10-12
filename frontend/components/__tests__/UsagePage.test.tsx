import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import UsagePage from '@/app/usage/page'

const { getUsage } = vi.hoisted(() => ({ getUsage: vi.fn() }))
vi.mock('@/lib/usage-api', () => ({ getUsage }))

describe('Usage page (SPEC-12)', () => {
  it('shows the budget, today\'s requests and spend by purpose', async () => {
    getUsage.mockResolvedValue({
      tier: 'free',
      on_quota: 'pause',
      tokens_used_24h: 12_000,
      tokens_remaining_24h: 188_000,
      daily_token_limit: 200_000,
      allow_ensemble: false,
      month_cost_usd: 0,
      monthly_cost_ceiling_usd: null,
      requests_left_today: 230,
      requests_per_day: 250,
      by_purpose_30d: [{ purpose: 'beat_prose', tokens: 9000, usd: 0, calls: 5 }],
      deployment_cost_30d_usd: 0,
    })
    render(<UsagePage />)
    expect(await screen.findByTestId('usage-page')).toBeTruthy()
    expect(screen.getByText('12,000 / 200,000')).toBeTruthy()
    expect(screen.getByText('20 / 250')).toBeTruthy()
    expect(screen.getByText('beat_prose')).toBeTruthy()
    expect(screen.getByText(/pauses and resumes/)).toBeTruthy()
  })

  it('handles a site with no generation yet', async () => {
    getUsage.mockResolvedValue({
      tier: 'paid', on_quota: 'fail', tokens_used_24h: 0, tokens_remaining_24h: 5_000_000,
      daily_token_limit: 5_000_000, allow_ensemble: true, month_cost_usd: 0,
      monthly_cost_ceiling_usd: 50, requests_left_today: null, requests_per_day: null,
      by_purpose_30d: [], deployment_cost_30d_usd: 1.5,
    })
    render(<UsagePage />)
    expect(await screen.findByText('No generation yet.')).toBeTruthy()
    expect(screen.getByText(/\$1\.50 of a \$50\.00 ceiling/)).toBeTruthy()
  })
})
