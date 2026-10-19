/**
 * Usage + cost estimates (SPEC-12, docs/BRD.md R7.5).
 *
 * Backend: app/api/usage.py (GET /usage) and app/api/estimates.py
 * (POST /estimate). The enqueue endpoints preflight with the same estimators,
 * so the number shown before a click is the number enforced after it.
 */

import apiClient, { toApiError } from './api-client'

export interface UsagePurpose {
  purpose: string
  tokens: number
  usd: number
  calls: number
}

export interface Usage {
  tier: string
  on_quota: string
  tokens_used_24h: number
  tokens_remaining_24h: number
  daily_token_limit: number
  allow_ensemble: boolean
  month_cost_usd: number
  monthly_cost_ceiling_usd: number | null
  requests_left_today: number | null
  requests_per_day: number | null
  by_purpose_30d: UsagePurpose[]
  deployment_cost_30d_usd: number
}

export type EstimateAction = 'scene' | 'outline' | 'continuity' | 'extraction'

export interface EstimateRequest {
  action: EstimateAction
  book_id?: string
  source_id?: string
  chapter_id?: string | null
  character_ids?: string[]
  target_word_count?: number
  mode?: 'prose' | 'ensemble' | 'staged' | 'single'
  max_chapters?: number
}

export interface Estimate {
  action: EstimateAction
  mode: string
  calls: number
  input_tokens: number
  output_tokens: number
  tokens: number
  usd: number | null // null: the model has no price (SPEC-81)
  degraded_from: string | null
  notes: string[]
  remaining_tokens_24h: number
  remaining_requests_today: number | null
  share_of_remaining: number | null
  allowed: boolean
  reason: string | null
}

export async function getUsage(): Promise<Usage> {
  try {
    const { data } = await apiClient.get<Usage>('/usage')
    return data
  } catch (err) {
    throw toApiError(err)
  }
}

export async function estimate(req: EstimateRequest): Promise<Estimate> {
  try {
    const { data } = await apiClient.post<Estimate>('/estimate', req)
    return data
  } catch (err) {
    throw toApiError(err)
  }
}

/** "≈ 6 calls · 31k tokens · 16% of today's budget" (+ "$0.04" when priced, "price unknown" when unpriced). */
export function describeEstimate(e: Estimate): string {
  const tokens =
    e.tokens >= 1000 ? `${Math.round(e.tokens / 1000)}k tokens` : `${e.tokens} tokens`
  const parts = [`≈ ${e.calls} call${e.calls === 1 ? '' : 's'}`, tokens]
  if (e.share_of_remaining !== null) {
    parts.push(`${Math.max(1, Math.round(e.share_of_remaining * 100))}% of today's budget`)
  }
  if (e.usd) parts.push(`$${e.usd.toFixed(2)}`)
  else if (e.usd === null) parts.push('price unknown')
  return parts.join(' · ')
}
