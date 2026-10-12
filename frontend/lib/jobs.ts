/**
 * Jobs API (SPEC-07/08): one lifecycle for every LLM job, read through
 * GET /jobs/{id} and cancelled through POST /jobs/{id}/cancel.
 *
 * The label helpers are pure so every panel renders a job the same way and
 * the wording is unit-testable without a DOM.
 */

import api, { toApiError } from './api-client'
import type { Job, JobProgress, JobStatus } from './types'

export const ACTIVE_JOB_STATUSES: JobStatus[] = ['queued', 'running', 'paused']

export function isActiveJob(status: string | null | undefined): boolean {
  return !!status && (ACTIVE_JOB_STATUSES as string[]).includes(status)
}

export async function getJob(jobId: string): Promise<Job> {
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    return data
  } catch (err) {
    throw toApiError(err)
  }
}

/** The author's queued, running and paused jobs (Usage page). */
export async function listActiveJobs(): Promise<Job[]> {
  try {
    const { data } = await api.get<{ jobs: Job[] }>('/jobs')
    return data.jobs
  } catch (err) {
    throw toApiError(err)
  }
}

export async function cancelJob(jobId: string): Promise<Job> {
  try {
    const { data } = await api.post<Job>(`/jobs/${jobId}/cancel`)
    return data
  } catch (err) {
    throw toApiError(err)
  }
}

/** "14:05" in the reader's local time. */
export function formatResumeTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

/** "beat 3 of 5" for scenes, "step 3 of 6" for anything else. */
export function progressLabel(
  progress: JobProgress | null | undefined,
  kind?: string
): string | null {
  if (!progress || !progress.total) return null
  const unit = kind && kind.includes('scene') ? 'beat' : 'step'
  return `${unit} ${Math.min(progress.done + 1, progress.total)} of ${progress.total}`
}

/** The badge text for a job state. */
export function jobStatusLabel(job: {
  status: JobStatus | string
  resume_at?: string | null
  progress?: JobProgress | null
  kind?: string
  cancel_requested?: boolean
}): string {
  switch (job.status) {
    case 'queued':
      return 'queued'
    case 'running': {
      if (job.cancel_requested) return 'cancelling…'
      const p = progressLabel(job.progress, job.kind)
      return p ? `running · ${p}` : 'running'
    }
    case 'paused': {
      const at = formatResumeTime(job.resume_at)
      return at ? `paused (resumes ~${at})` : 'paused'
    }
    case 'succeeded':
      return 'done'
    case 'failed':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    default:
      return String(job.status)
  }
}

export const JOB_STATUS_STYLES: Record<string, string> = {
  queued: 'bg-gray-100 text-gray-700',
  running: 'bg-yellow-100 text-yellow-700',
  paused: 'bg-indigo-100 text-indigo-700',
  succeeded: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-600',
}
