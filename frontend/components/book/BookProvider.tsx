/**
 * BookProvider — the book detail page's shared state (SPEC-15d).
 *
 * The panels under `components/book/` all need the same few things: the book
 * id from `?id=`, the loaded book and its chapters, the book's characters, and
 * a way to refetch them. They used to be prop-drilled from the page; the page
 * now wraps its tabs in a `BookProvider` and each panel reads them with
 * `useBook()`.
 *
 * Deliberately minimal — no new state library. The book itself still lives in
 * the zustand `useBookStore` (other pages read it too); this provider only
 * scopes it to one book id and loads it. Characters are held here but loaded
 * lazily (`refreshCharacters`), so opening the page makes no extra request.
 */

'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useBookStore } from '@/lib/books-store'
import { canonApi } from '@/lib/canon-store'
import type {
  BookChapter,
  BookDetail,
  ChapterDetail,
  Character,
} from '@/lib/types'

export interface BookContextValue {
  /** The book id from `?id=` ('' when absent). */
  bookId: string
  /** The loaded book, or null before it loads / when it failed. */
  book: BookDetail | null
  /** `book.chapters`, or [] before the book loads. */
  chapters: BookChapter[]
  /** The book's characters; [] until `refreshCharacters` has run. */
  characters: Character[]
  isLoading: boolean
  error: string | null
  /** Refetch the book (header, synopsis, chapters). */
  refreshBook: () => Promise<BookDetail>
  /** Refetch one chapter (its scenes) into the loaded book. */
  refreshChapter: (chapterId: string) => Promise<ChapterDetail>
  /** (Re)load the book's characters; resolves with them, rejects on failure. */
  refreshCharacters: () => Promise<Character[]>
}

/** Exported so tests can supply a value without the store or network. */
export const BookContext = createContext<BookContextValue | null>(null)

export function BookProvider({
  bookId,
  children,
}: {
  bookId: string
  children: ReactNode
}) {
  const { currentBook, isLoading, error, fetchBook, fetchChapter } = useBookStore()
  const [characters, setCharacters] = useState<Character[]>([])

  // Latest id, so a slow characters response for a previous book is dropped.
  const latestId = useRef(bookId)
  latestId.current = bookId

  useEffect(() => {
    setCharacters([])
    if (bookId) {
      fetchBook(bookId).catch(() => {
        // error surfaced via store state
      })
    }
  }, [bookId, fetchBook])

  const refreshBook = useCallback(() => fetchBook(bookId), [bookId, fetchBook])

  const refreshCharacters = useCallback(async () => {
    const chars = await canonApi.listCharacters(bookId)
    if (latestId.current === bookId) setCharacters(chars)
    return chars
  }, [bookId])

  const value = useMemo<BookContextValue>(
    () => ({
      bookId,
      book: currentBook,
      chapters: currentBook?.chapters ?? [],
      characters,
      isLoading,
      error,
      refreshBook,
      refreshChapter: fetchChapter,
      refreshCharacters,
    }),
    [
      bookId,
      currentBook,
      characters,
      isLoading,
      error,
      refreshBook,
      fetchChapter,
      refreshCharacters,
    ]
  )

  return <BookContext.Provider value={value}>{children}</BookContext.Provider>
}

/** The current book's shared state. Must be used under a `BookProvider`. */
export function useBook(): BookContextValue {
  const ctx = useContext(BookContext)
  if (!ctx) throw new Error('useBook() must be used within a <BookProvider>')
  return ctx
}
