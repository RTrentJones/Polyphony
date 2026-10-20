import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SourcesPanel from '@/components/book/SourcesPanel'
import { bookValue, renderWithBook } from './bookContext'
import type { Source } from '@/lib/types'

const { fetchSources, startExtraction, store } = vi.hoisted(() => ({
  fetchSources: vi.fn(),
  startExtraction: vi.fn(),
  store: { sources: [] as unknown[] },
}))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/lib/api-client', () => ({ default: { getSource: vi.fn() } }))
vi.mock('@/lib/store', () => ({
  useSourceStore: () => ({
    sources: store.sources,
    fetchSources,
    editSource: vi.fn(),
    deleteSource: vi.fn(),
  }),
  useExtractionStore: () => ({ startExtraction }),
}))

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: 'src-1',
    book_id: 'b',
    title: 'Bored to Undeath',
    kind: 'upload',
    word_count: 1200,
    status: 'completed',
    latest_extraction: null,
    ...overrides,
  } as Source
}

function renderPanel(...sources: Source[]) {
  store.sources = sources
  return renderWithBook(<SourcesPanel addToast={vi.fn()} />, bookValue({ bookId: 'b' }))
}

describe('SourcesPanel Extract control', () => {
  beforeEach(() => {
    fetchSources.mockReset().mockResolvedValue(undefined)
    startExtraction.mockReset()
  })

  it('offers Extract on a source with no extraction', () => {
    renderPanel(source())
    expect(screen.getByRole('button', { name: 'Extract' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Re-extract' })).not.toBeInTheDocument()
  })

  it('offers Re-extract (and says so) after a failed extraction', () => {
    renderPanel(source({ latest_extraction: { id: 'run-1', status: 'failed', job: null } }))
    expect(screen.getByRole('button', { name: 'Re-extract' })).toBeInTheDocument()
    expect(screen.getByText('Extraction failed')).toBeInTheDocument()
  })

  it('is hidden while extracting or pending review', () => {
    renderPanel(
      source({
        id: 'running',
        latest_extraction: {
          id: 'run-1',
          status: 'pending',
          job: { job_id: 'job-1', status: 'running' },
        } as Source['latest_extraction'],
      }),
      source({
        id: 'ready',
        title: 'Second',
        latest_extraction: { id: 'run-2', status: 'ready', job: null },
      })
    )
    expect(screen.queryByRole('button', { name: /extract/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument()
  })

  it('starts extraction for (bookId, sourceId) and refreshes the list', async () => {
    startExtraction.mockResolvedValue({ run_id: 'run-9', status: 'pending' })
    renderPanel(source())
    fetchSources.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Extract' }))
    await waitFor(() => expect(startExtraction).toHaveBeenCalledWith('b', 'src-1', undefined))
    await waitFor(() => expect(fetchSources).toHaveBeenCalledWith('b'))
  })

  it('shows the API error inline on the row', async () => {
    startExtraction.mockRejectedValue({
      message: 'Daily LLM token budget exhausted (10/10). Try again later.',
      status: 429,
      detail: 'Daily LLM token budget exhausted (10/10). Try again later.',
    })
    renderPanel(source())
    fireEvent.click(screen.getByRole('button', { name: 'Extract' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Daily LLM token budget/)
  })

  it('a preflight refusal offers the first N chapters', async () => {
    startExtraction
      .mockRejectedValueOnce({
        message: 'too large',
        status: 429,
        detail: {
          message: 'too large',
          preflight: {
            windows: 40,
            map_calls: 40,
            reduce_calls: 3,
            requests: 43,
            estimated_tokens: 900000,
            capacity_tokens: 300000,
            fits: false,
            max_chapters: 6,
          },
        },
      })
      .mockResolvedValueOnce({ run_id: 'run-9', status: 'pending' })
    renderPanel(source())
    fireEvent.click(screen.getByRole('button', { name: 'Extract' }))
    const opening = await screen.findByRole('button', {
      name: 'Extract the first 6 chapters',
    })
    fireEvent.click(opening)
    await waitFor(() => expect(startExtraction).toHaveBeenLastCalledWith('b', 'src-1', 6))
  })
})
