'use client'

/**
 * JobStatusBadge — the one rendering of a job's state (SPEC-07), shared by
 * scenes, outline, extraction and continuity: queued, running (with SPEC-08
 * progress), paused "resumes ~HH:MM", failed, cancelled — and a Cancel
 * button while the job can still be cancelled.
 *
 * `initial` is the summary embedded in the list payload, shown until the
 * first poll lands, so the badge never flashes empty.
 */

import { useEffect, useState } from 'react'

import { JOB_STATUS_STYLES, isActiveJob, jobStatusLabel } from '@/lib/jobs'
import type { Job, JobSummary } from '@/lib/types'

import { useJob } from './useJob'

interface Props {
  jobId: string | null | undefined
  initial?: JobSummary | null
  /** Called once when the job reaches a terminal state (incl. after Cancel). */
  onSettled?: (job: Job) => void
  /** Called with each new live status, so a row can describe a pause the
   *  moment it happens rather than when its list is next fetched. */
  onStatus?: (status: Job['status']) => void
}

export default function JobStatusBadge({ jobId, initial, onSettled, onStatus }: Props) {
  const { job, cancel } = useJob(jobId, { onSettled })
  const status = job?.status
  useEffect(() => {
    if (status) onStatus?.(status)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  if (!jobId) return null

  const shown = job ?? initial
  if (!shown) return null
  const label = jobStatusLabel(shown)
  const cancellable = job ? job.cancellable : isActiveJob(shown.status)

  const onCancel = async () => {
    setCancelling(true)
    setCancelError(null)
    try {
      await cancel()
    } catch (e) {
      setCancelError((e as { message?: string }).message || 'Could not cancel')
    } finally {
      setCancelling(false)
    }
  }

  return (
    <span className="inline-flex items-center gap-1" data-testid="job-status">
      <span
        className={`px-2 py-0.5 text-xs rounded ${
          JOB_STATUS_STYLES[shown.status] || 'bg-gray-100 text-gray-600'
        }`}
        title={job?.error || undefined}
      >
        {label}
      </span>
      {cancellable && (
        <button
          type="button"
          onClick={onCancel}
          disabled={cancelling}
          className="text-xs text-gray-500 hover:text-red-600 underline disabled:opacity-50"
        >
          {cancelling ? 'Cancelling…' : 'Cancel'}
        </button>
      )}
      {cancelError && <span className="text-xs text-red-600">{cancelError}</span>}
    </span>
  )
}
