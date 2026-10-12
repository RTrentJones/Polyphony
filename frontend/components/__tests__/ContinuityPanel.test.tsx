import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ContinuityPanel from '@/components/book/ContinuityPanel'
import type { ContinuityReport } from '@/lib/types'

const planning = vi.hoisted(() => ({
  reports: [] as ContinuityReport[],
  fetchContinuityReports: vi.fn(),
  runContinuityCheck: vi.fn(),
}))

vi.mock('@/lib/books-store', () => ({ usePlanningStore: () => planning }))

// A report with a live job renders the shared JobStatusBadge (SPEC-07); keep its
// poll pending so the badge shows the list's summary.
vi.mock('@/lib/jobs', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/lib/jobs')>()
  return { ...real, getJob: () => new Promise(() => {}), cancelJob: vi.fn() }
})

function report(overrides: Partial<ContinuityReport>): ContinuityReport {
  return {
    id: 'r-1',
    scope: 'book',
    chapter_id: null,
    status: 'completed',
    findings: [],
    created_at: null,
    ...overrides,
  }
}

function renderWith(reports: ContinuityReport[]) {
  planning.reports = reports
  // Resolve with no in-flight reports so the panel does not start polling.
  planning.fetchContinuityReports.mockResolvedValue([])
  render(<ContinuityPanel bookId="book-1" chapters={[]} addToast={() => {}} />)
}

describe('ContinuityPanel report status', () => {
  beforeEach(() => {
    planning.fetchContinuityReports.mockReset()
  })

  it('completed with no findings is a clean pass', () => {
    renderWith([report({ status: 'completed' })])
    expect(screen.getByText('completed')).toHaveClass('bg-green-100')
    expect(screen.getByText(/No contradictions found\. Clean pass\./)).toBeInTheDocument()
    expect(screen.queryByTestId('coverage')).not.toBeInTheDocument()
  })

  it('completed with findings lists them by severity', () => {
    renderWith([
      report({
        findings: [
          { severity: 'minor', type: 'name', detail: 'Spelling drift' },
          { severity: 'critical', type: 'timeline', detail: 'Dead man walks' },
        ] as ContinuityReport['findings'],
      }),
    ])
    const details = screen.getAllByText(/Spelling drift|Dead man walks/)
    expect(details.map((d) => d.textContent)).toEqual(['Dead man walks', 'Spelling drift'])
  })

  it('incomplete shows honest coverage, not a clean pass', () => {
    renderWith([
      report({
        status: 'incomplete',
        coverage: { chunks_checked: 3, chunks_total: 5, unchecked: ['ch 4', 'ch 5'] },
      }),
    ])
    expect(screen.getByText('incomplete')).toHaveClass('bg-amber-100')
    const coverage = screen.getByTestId('coverage')
    expect(coverage).toHaveTextContent('Checked 3 of 5 sections')
    expect(coverage).toHaveTextContent('ch 4, ch 5')
    expect(
      screen.getByText('No contradictions in the sections that were checked.')
    ).toBeInTheDocument()
    expect(screen.queryByText(/Clean pass/)).not.toBeInTheDocument()
  })

  it('paused says it is waiting for quota', () => {
    renderWith([report({ status: 'paused', findings: null })])
    expect(screen.getByText('paused')).toHaveClass('bg-yellow-100')
    expect(screen.getByText(/Waiting for AI quota/)).toBeInTheDocument()
  })

  it('failed says so', () => {
    renderWith([report({ status: 'failed', findings: null })])
    expect(screen.getByText('failed')).toHaveClass('bg-red-100')
    expect(screen.getByText(/This check failed/)).toBeInTheDocument()
  })

  it('renders one card per report', () => {
    renderWith([
      report({ id: 'a', status: 'completed' }),
      report({ id: 'b', status: 'failed', findings: null }),
    ])
    const container = screen.getByText('completed').closest('.space-y-4') as HTMLElement
    expect(within(container).getByText('failed')).toBeInTheDocument()
  })

  it('a report with a live paused job shows the resume time and Cancel', () => {
    const at = new Date(2026, 9, 12, 14, 5).toISOString()
    renderWith([
      report({
        status: 'paused',
        findings: null,
        job: { job_id: 'j-1', status: 'paused', resume_at: at, progress: null },
      }),
    ])
    expect(screen.getByText('paused (resumes ~14:05)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByText(/Waiting for AI quota/)).toBeInTheDocument()
  })

  it('cancelled says so', () => {
    renderWith([report({ status: 'cancelled', findings: null })])
    expect(screen.getByText('This check was cancelled.')).toBeInTheDocument()
  })
})
