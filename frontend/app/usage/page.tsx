'use client'

/**
 * Usage (SPEC-12): today's token budget, the provider's requests left today,
 * and where the last 30 days of spend went — so the free tier is legible and
 * graduating to paid is an informed choice (BRD R7.5, R7.6).
 */

import { useEffect, useState } from 'react'
import { Gauge } from 'lucide-react'

import Card from '@/components/Card'
import JobStatusBadge from '@/components/JobStatusBadge'
import Loading from '@/components/Loading'
import { listActiveJobs } from '@/lib/jobs'
import type { Job } from '@/lib/types'
import { getUsage, type Usage } from '@/lib/usage-api'

function Meter({ used, total, label }: { used: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0
  return (
    <div>
      <div className="flex justify-between text-sm mb-1">
        <span className="text-gray-700">{label}</span>
        <span className="text-gray-500">
          {used.toLocaleString()} / {total.toLocaleString()}
        </span>
      </div>
      <div className="h-2 bg-gray-100 rounded" role="meter" aria-valuenow={pct}>
        <div
          className={`h-2 rounded ${pct > 85 ? 'bg-red-500' : 'bg-primary-500'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])

  useEffect(() => {
    getUsage()
      .then(setUsage)
      .catch((e) => setError(e.message || 'Failed to load usage'))
    listActiveJobs()
      .then(setJobs)
      .catch(() => setJobs([]))
  }, [])

  if (error) return <p className="max-w-4xl mx-auto p-6 text-red-600">{error}</p>
  if (!usage) return <Loading text="Loading usage..." />

  const tokensUsed = usage.tokens_used_24h

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6" data-testid="usage-page">
      <div className="flex items-center gap-2">
        <Gauge className="h-6 w-6 text-primary-600" />
        <h1 className="text-2xl font-bold text-gray-900">Usage</h1>
        <span className="ml-2 px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-700">
          {usage.tier} tier
        </span>
      </div>

      <Card>
        <div className="space-y-4">
          <Meter
            used={tokensUsed}
            total={usage.daily_token_limit}
            label="Your tokens, rolling 24 hours"
          />
          {Object.entries(usage.models).map(
            ([model, r]) =>
              r.left !== null &&
              r.per_day !== null && (
                <Meter
                  key={model}
                  used={r.per_day - r.left}
                  total={r.per_day}
                  label={`${model} requests today (shared by everyone on this site)`}
                />
              )
          )}
          <p className="text-sm text-gray-600">
            {usage.on_quota === 'pause'
              ? 'When the free quota runs out, generation pauses and resumes on its own — nothing is lost.'
              : 'On the paid tier a quota error fails the job so you can retry.'}{' '}
            {usage.allow_ensemble
              ? 'Ensemble scenes are available.'
              : 'Ensemble scenes need the paid tier; scenes run in prose mode.'}
          </p>
        </div>
      </Card>

      <Card>
        <h2 className="font-semibold text-gray-900 mb-3">Waiting and in progress</h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-gray-500">Nothing queued.</p>
        ) : (
          <ul className="space-y-2" data-testid="active-jobs">
            {jobs.map((j) => (
              <li key={j.id} className="flex items-center justify-between text-sm">
                <span className="text-gray-700">{j.kind.replace(/_/g, ' ')}</span>
                <JobStatusBadge
                  jobId={j.id}
                  initial={{ job_id: j.id, status: j.status, resume_at: j.resume_at, progress: j.progress }}
                  onSettled={(done) =>
                    setJobs((js) => js.filter((x) => x.id !== done.id))
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="font-semibold text-gray-900 mb-3">Last 30 days</h2>
        <p className="text-sm text-gray-600 mb-3">
          Your spend ${usage.month_cost_usd.toFixed(2)}
          {usage.monthly_cost_ceiling_usd !== null &&
            ` · site-wide $${usage.deployment_cost_30d_usd.toFixed(2)} of a $${usage.monthly_cost_ceiling_usd.toFixed(2)} ceiling`}
        </p>
        {usage.by_purpose_30d.length === 0 ? (
          <p className="text-sm text-gray-500">No generation yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="py-1">What</th>
                <th className="py-1 text-right">Calls</th>
                <th className="py-1 text-right">Tokens</th>
                <th className="py-1 text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.by_purpose_30d.map((p) => (
                <tr key={p.purpose} className="border-t border-gray-100">
                  <td className="py-1">{p.purpose}</td>
                  <td className="py-1 text-right">{p.calls}</td>
                  <td className="py-1 text-right">{p.tokens.toLocaleString()}</td>
                  <td className="py-1 text-right">${p.usd.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
