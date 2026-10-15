/** The guided empty book (SPEC-27, B12). */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, listCharacters, listBookScenes } = vi.hoisted(() => ({
  get: vi.fn(),
  listCharacters: vi.fn(),
  listBookScenes: vi.fn(),
}))

vi.mock('@/lib/api-client', () => ({
  default: { get },
  toApiError: (e: unknown) => e,
}))
vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters } }))
vi.mock('@/lib/books-store', () => ({ booksApi: { listBookScenes } }))

import GettingStarted, {
  GettingStartedChecklist,
  type GettingStartedProgress,
} from '@/components/book/GettingStarted'

const empty: GettingStartedProgress = {
  sources: 0,
  characters: 0,
  outline: false,
  scenes: 0,
}

const READY_OUTLINE = [{ kind: 'outline', status: 'ready', content: [{ title: 'One' }] }]

function serve({ sources = 0, plans = [] as unknown[] } = {}) {
  get.mockImplementation((url: string) => {
    if (url === '/sources/')
      return Promise.resolve({
        data: {
          sources: Array.from({ length: sources }, (_, i) => ({
            id: `s-${i}`,
            book_id: 'book-1',
          })),
        },
      })
    if (url === '/books/book-1/plans') return Promise.resolve({ data: { plans } })
    return Promise.reject(new Error(`unexpected ${url}`))
  })
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('GettingStartedChecklist', () => {
  it("never reuses a tab's or the page's button names", () => {
    render(
      <GettingStartedChecklist progress={empty} onAction={vi.fn()} onDismiss={vi.fn()} />
    )
    const names = screen.getAllByRole('button').map((b) => b.textContent ?? '')
    const taken = /add source|chapters|scenes|sources|canon|outline|generate|threads|trash/i
    expect(names.filter((n) => taken.test(n))).toEqual([])
  })

  it('reads 0/4 on an empty book, with Add source as the next step', () => {
    const onAction = vi.fn()
    render(
      <GettingStartedChecklist progress={empty} onAction={onAction} onDismiss={vi.fn()} />
    )
    expect(screen.getByTestId('getting-started-progress')).toHaveTextContent('0/4 done')
    fireEvent.click(screen.getByRole('button', { name: 'Add your first source' }))
    expect(onAction).toHaveBeenCalledWith('add-source')
  })

  it('reads 1/4 after a source is added, and deep-links the rest', () => {
    const onAction = vi.fn()
    render(
      <GettingStartedChecklist
        progress={{ ...empty, sources: 1 }}
        onAction={onAction}
        onDismiss={vi.fn()}
      />
    )
    expect(screen.getByTestId('getting-started-progress')).toHaveTextContent('1/4 done')
    expect(
      screen.queryByRole('button', { name: 'Add your first source' })
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Review the cast' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start planning' }))
    fireEvent.click(screen.getByRole('button', { name: 'Write a scene' }))
    expect(onAction.mock.calls.map((c) => c[0])).toEqual(['canon', 'outline', 'chapters'])
  })
})

describe('GettingStarted (ticks from real data)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    get.mockReset()
    listCharacters.mockReset()
    listBookScenes.mockReset()
    listCharacters.mockResolvedValue([])
    listBookScenes.mockResolvedValue([])
  })

  it('reads 0/4, then 1/4 once the book has a source', async () => {
    serve()
    const { rerender } = render(
      <GettingStarted bookId="book-1" refreshKey="a" onAction={vi.fn()} />
    )
    expect(await screen.findByTestId('getting-started-progress')).toHaveTextContent(
      '0/4 done'
    )

    serve({ sources: 1 })
    rerender(<GettingStarted bookId="book-1" refreshKey="b" onAction={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId('getting-started-progress')).toHaveTextContent('1/4 done')
    )
  })

  it('counts a ready outline and a completed scene, not a failed one', async () => {
    serve({ sources: 1, plans: READY_OUTLINE })
    listCharacters.mockResolvedValue([{ id: 'c-1', name: 'Milo', book_id: 'book-1' }])
    listBookScenes.mockResolvedValue([{ id: 's-1', status: 'failed' }])
    render(<GettingStarted bookId="book-1" onAction={vi.fn()} />)
    expect(await screen.findByTestId('getting-started-progress')).toHaveTextContent(
      '3/4 done'
    )
  })

  it('hides once all four are done', async () => {
    serve({ sources: 1, plans: READY_OUTLINE })
    listCharacters.mockResolvedValue([{ id: 'c-1', name: 'Milo', book_id: 'book-1' }])
    listBookScenes.mockResolvedValue([{ id: 's-1', status: 'completed' }])
    render(<GettingStarted bookId="book-1" onAction={vi.fn()} />)
    await waitFor(() => expect(listBookScenes).toHaveBeenCalled())
    await flush()
    expect(screen.queryByTestId('getting-started')).not.toBeInTheDocument()
  })

  it('stays dismissed for that book', async () => {
    serve()
    const { unmount } = render(<GettingStarted bookId="book-1" onAction={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss getting started' }))
    expect(screen.queryByTestId('getting-started')).not.toBeInTheDocument()
    unmount()

    render(<GettingStarted bookId="book-1" onAction={vi.fn()} />)
    await waitFor(() => expect(get).toHaveBeenCalledTimes(4))
    await flush()
    expect(screen.queryByTestId('getting-started')).not.toBeInTheDocument()
  })

  it('shows nothing when progress cannot be loaded', async () => {
    get.mockRejectedValue(new Error('down'))
    render(<GettingStarted bookId="book-1" onAction={vi.fn()} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    await flush()
    expect(screen.queryByTestId('getting-started')).not.toBeInTheDocument()
  })
})
