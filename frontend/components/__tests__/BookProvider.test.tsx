import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BookProvider, useBook } from '@/components/book/BookProvider'
import type { BookDetail, Character } from '@/lib/types'

const store = vi.hoisted(() => ({
  currentBook: null as unknown,
  isLoading: false,
  error: null as string | null,
  fetchBook: vi.fn(),
  fetchChapter: vi.fn(),
}))
vi.mock('@/lib/books-store', () => ({ useBookStore: () => store }))

const { listCharacters } = vi.hoisted(() => ({ listCharacters: vi.fn() }))
vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters } }))

const book = {
  id: 'book-1',
  title: 'The Heist',
  synopsis: 'A vault job.',
  status: 'drafting',
  chapters: [{ id: 'ch-1', title: 'Opening', position: 0 }],
} as unknown as BookDetail

const milo = { id: 'c-1', name: 'Milo Voss', book_id: 'book-1' } as Character

function wrapperFor(bookId: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <BookProvider bookId={bookId}>{children}</BookProvider>
  }
}

describe('BookProvider / useBook (SPEC-15d)', () => {
  beforeEach(() => {
    store.currentBook = null
    store.isLoading = false
    store.error = null
    store.fetchBook.mockReset().mockResolvedValue(book)
    store.fetchChapter.mockReset().mockResolvedValue({})
    listCharacters.mockReset()
  })

  it('loads the book for its id and exposes it, its chapters and the id', async () => {
    store.currentBook = book
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('book-1') })
    await waitFor(() => expect(store.fetchBook).toHaveBeenCalledWith('book-1'))
    expect(result.current.bookId).toBe('book-1')
    expect(result.current.book).toBe(book)
    expect(result.current.chapters).toBe(book.chapters)
    // Characters load lazily: opening the page makes no extra request.
    expect(result.current.characters).toEqual([])
    expect(listCharacters).not.toHaveBeenCalled()
  })

  it('does not fetch without an id, and has no chapters before the book loads', () => {
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('') })
    expect(store.fetchBook).not.toHaveBeenCalled()
    expect(result.current.book).toBeNull()
    expect(result.current.chapters).toEqual([])
  })

  it('swallows a failed load (the store carries the error)', async () => {
    store.fetchBook.mockReset().mockRejectedValue(new Error('nope'))
    store.error = 'nope'
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('book-1') })
    await waitFor(() => expect(store.fetchBook).toHaveBeenCalled())
    expect(result.current.error).toBe('nope')
  })

  it('refreshBook and refreshChapter refetch through the store', async () => {
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('book-1') })
    store.fetchBook.mockClear()
    await act(async () => {
      await result.current.refreshBook()
      await result.current.refreshChapter('ch-1')
    })
    expect(store.fetchBook).toHaveBeenCalledWith('book-1')
    expect(store.fetchChapter).toHaveBeenCalledWith('ch-1')
  })

  it('refreshCharacters loads the book cast into the context', async () => {
    listCharacters.mockResolvedValue([milo])
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('book-1') })
    let returned: Character[] = []
    await act(async () => {
      returned = await result.current.refreshCharacters()
    })
    expect(listCharacters).toHaveBeenCalledWith('book-1')
    expect(returned).toEqual([milo])
    expect(result.current.characters).toEqual([milo])
  })

  it('refreshCharacters rejects when the request fails', async () => {
    listCharacters.mockRejectedValue(new Error('down'))
    const { result } = renderHook(() => useBook(), { wrapper: wrapperFor('book-1') })
    await expect(result.current.refreshCharacters()).rejects.toThrow('down')
    expect(result.current.characters).toEqual([])
  })

  it('a consumer reads the book without props', () => {
    store.currentBook = book
    function Title() {
      const { book: b, chapters } = useBook()
      return (
        <p>
          {b?.title} ({chapters.length})
        </p>
      )
    }
    render(
      <BookProvider bookId="book-1">
        <Title />
      </BookProvider>
    )
    expect(screen.getByText('The Heist (1)')).toBeInTheDocument()
  })

  it('useBook outside a provider throws', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => renderHook(() => useBook())).toThrow(/within a <BookProvider>/)
    spy.mockRestore()
  })
})
