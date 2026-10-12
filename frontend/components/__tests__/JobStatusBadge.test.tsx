import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import JobStatusBadge from '@/components/JobStatusBadge'
import { nextPollDelay } from '@/components/useJob'
import { formatResumeTime, jobStatusLabel, progressLabel } from '@/lib/jobs'
import type { Job } from '@/lib/types'

const api = vi.hoisted(() => ({ getJob: vi.fn(), cancelJob: vi.fn() }))

vi.mock('@/lib/jobs', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/jobs')>()
  return { ...real, getJob: api.getJob, cancelJob: api.cancelJob }
})

function job(overrides: Partial<Job>): Job {
  return {
    id: 'j-1',
    kind: 'generate_prose_scene',
    status: 'running',
    available_at: null,
    resume_at: null,
    attempts: 1,
    max_attempts: 1,
    pause_count: 0,
    cancel_requested: false,
    cancellable: true,
    error: null,
    progress: null,
    domain: { type: 'scene', id: 's-1' },
    created_at: null,
    finished_at: null,
    ...overrides,
  }
}

describe('job labels', () => {
  it('renders every status the same way everywhere', () => {
    expect(jobStatusLabel({ status: 'queued' })).toBe('queued')
    expect(jobStatusLabel({ status: 'failed' })).toBe('failed')
    expect(jobStatusLabel({ status: 'cancelled' })).toBe('cancelled')
    expect(jobStatusLabel({ status: 'running', cancel_requested: true })).toBe(
      'cancelling…'
    )
  })

  it('a paused job says when it resumes', () => {
    const at = new Date(2026, 9, 12, 14, 5).toISOString()
    expect(formatResumeTime(at)).toBe('14:05')
    expect(jobStatusLabel({ status: 'paused', resume_at: at })).toBe(
      'paused (resumes ~14:05)'
    )
    expect(jobStatusLabel({ status: 'paused', resume_at: null })).toBe('paused')
  })

  it('a running job shows its checkpoint progress', () => {
    expect(progressLabel({ done: 2, total: 5 }, 'generate_prose_scene')).toBe(
      'beat 3 of 5'
    )
    expect(progressLabel({ done: 3, total: 4 }, 'generate_outline')).toBe('step 4 of 4')
    expect(
      jobStatusLabel({
        status: 'running',
        kind: 'generate_prose_scene',
        progress: { done: 2, total: 5 },
      })
    ).toBe('running · beat 3 of 5')
  })

  it('polls a paused job no sooner than its resume time allows', () => {
    const soon = new Date(Date.now() + 10_000).toISOString()
    expect(nextPollDelay(job({ status: 'paused', resume_at: soon }), 2000)).toBeLessThanOrEqual(
      11_000
    )
    expect(nextPollDelay(job({ status: 'paused', resume_at: null }), 2000)).toBe(60_000)
    expect(nextPollDelay(job({ status: 'running' }), 20_000)).toBe(30_000)
  })
})

describe('JobStatusBadge', () => {
  beforeEach(() => {
    api.getJob.mockReset()
    api.cancelJob.mockReset()
  })

  it('shows the list summary first, then the polled job', async () => {
    api.getJob.mockResolvedValue(job({ status: 'queued' }))
    render(
      <JobStatusBadge
        jobId="j-1"
        initial={{ job_id: 'j-1', status: 'running', resume_at: null, progress: null }}
      />
    )
    expect(screen.getByText('running')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('queued')).toBeInTheDocument())
  })

  it('cancel calls the API and reports the settled job', async () => {
    api.getJob.mockResolvedValue(job({ status: 'paused' }))
    api.cancelJob.mockResolvedValue(job({ status: 'cancelled', cancellable: false }))
    const onSettled = vi.fn()
    render(<JobStatusBadge jobId="j-1" onSettled={onSettled} />)
    await waitFor(() => expect(screen.getByText('paused')).toBeInTheDocument())
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    })
    expect(api.cancelJob).toHaveBeenCalledWith('j-1')
    await waitFor(() => expect(screen.getByText('cancelled')).toBeInTheDocument())
    expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ status: 'cancelled' }))
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('renders nothing without a job', () => {
    const { container } = render(<JobStatusBadge jobId={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
