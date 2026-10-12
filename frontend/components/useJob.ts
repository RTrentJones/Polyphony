'use client'

/**
 * useJob(jobId) — poll GET /jobs/{id} with backoff until the job settles.
 *
 * Starts quick (2 s) and backs off to 30 s while the job runs; a paused job is
 * waiting for quota, so it is checked once a minute (or at its resume time,
 * whichever is sooner). Polling stops at a terminal state and `onSettled`
 * fires once, so the owning panel can refetch its domain row.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { cancelJob, getJob, isActiveJob } from '@/lib/jobs'
import type { Job } from '@/lib/types'

const FIRST_DELAY_MS = 2000
const MAX_DELAY_MS = 30000
const PAUSED_DELAY_MS = 60000

export function nextPollDelay(job: Job | null, previous: number): number {
  if (job?.status === 'paused') {
    const due = job.resume_at ? new Date(job.resume_at).getTime() - Date.now() : NaN
    return Number.isFinite(due) && due > 0
      ? Math.min(PAUSED_DELAY_MS, due + 1000)
      : PAUSED_DELAY_MS
  }
  return Math.min(MAX_DELAY_MS, Math.round(previous * 1.5))
}

export function useJob(
  jobId: string | null | undefined,
  options: { onSettled?: (job: Job) => void } = {}
) {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const delay = useRef(FIRST_DELAY_MS)
  const onSettled = useRef(options.onSettled)
  onSettled.current = options.onSettled

  const poll = useCallback(async () => {
    if (!jobId) return
    try {
      const fresh = await getJob(jobId)
      setJob(fresh)
      setError(null)
      if (isActiveJob(fresh.status)) {
        delay.current = nextPollDelay(fresh, delay.current)
        timer.current = setTimeout(poll, delay.current)
      } else {
        onSettled.current?.(fresh)
      }
    } catch (e) {
      setError((e as { message?: string }).message || 'Could not load job status')
      // Transient (network, 429 already retried): keep trying, slowly.
      delay.current = MAX_DELAY_MS
      timer.current = setTimeout(poll, delay.current)
    }
  }, [jobId])

  useEffect(() => {
    delay.current = FIRST_DELAY_MS
    setJob(null)
    if (jobId) poll()
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [jobId, poll])

  const cancel = useCallback(async () => {
    if (!jobId) return
    const updated = await cancelJob(jobId)
    setJob(updated)
    if (!isActiveJob(updated.status)) {
      if (timer.current) clearTimeout(timer.current)
      onSettled.current?.(updated)
    }
  }, [jobId])

  return { job, error, cancel, refresh: poll }
}
