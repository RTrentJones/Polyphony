'use client'

/**
 * Cost before action (SPEC-12, BRD R7.5): shows what an LLM-spending button
 * will cost, debounced as the form changes, and why it is disabled if it is.
 */

import { useEffect, useRef, useState } from 'react'
import { Gauge } from 'lucide-react'

import {
  describeEstimate,
  estimate,
  type Estimate,
  type EstimateRequest,
} from '@/lib/usage-api'

export interface EstimateHintProps {
  request: EstimateRequest | null
  /** Reports the latest estimate so the caller can disable its button. */
  onEstimate?: (e: Estimate | null) => void
  debounceMs?: number
}

export default function EstimateHint({
  request,
  onEstimate,
  debounceMs = 400,
}: EstimateHintProps) {
  const [est, setEst] = useState<Estimate | null>(null)
  const [failed, setFailed] = useState(false)
  const key = request ? JSON.stringify(request) : ''
  const cb = useRef(onEstimate)
  cb.current = onEstimate

  useEffect(() => {
    if (!request) {
      setEst(null)
      cb.current?.(null)
      return
    }
    let cancelled = false
    const t = setTimeout(async () => {
      let e: Estimate | null = null
      try {
        e = await estimate(request)
      } catch {
        // An estimate is advisory: never block the action on a failed estimate.
        e = null
      }
      if (cancelled) return
      setEst(e)
      setFailed(e === null)
      cb.current?.(e)
    }, debounceMs)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, debounceMs])

  if (failed || !est) return null
  return (
    <div className="text-sm space-y-1" data-testid="estimate">
      <p className={est.allowed ? 'text-gray-600' : 'text-red-600'}>
        <Gauge className="inline h-4 w-4 mr-1" />
        {describeEstimate(est)}
        {est.degraded_from ? ` · runs as ${est.mode}` : ''}
      </p>
      {!est.allowed && est.reason && (
        <p className="text-red-600" data-testid="estimate-reason">
          {est.reason}
        </p>
      )}
      {est.notes.map((n, i) => (
        <p key={i} className="text-xs text-gray-500">
          {n}
        </p>
      ))}
    </div>
  )
}
