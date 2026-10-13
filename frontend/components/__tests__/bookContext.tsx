/** Test helper: render a book panel under a stub `BookContext` (SPEC-15d). */

import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'

import { BookContext, type BookContextValue } from '@/components/book/BookProvider'

export function bookValue(overrides: Partial<BookContextValue> = {}): BookContextValue {
  return {
    bookId: 'book-1',
    book: null,
    chapters: [],
    characters: [],
    isLoading: false,
    error: null,
    refreshBook: vi.fn().mockResolvedValue(undefined),
    refreshChapter: vi.fn().mockResolvedValue(undefined),
    refreshCharacters: vi.fn().mockResolvedValue([]),
    ...overrides,
  }
}

export function renderWithBook(ui: ReactElement, value: BookContextValue = bookValue()) {
  render(<BookContext.Provider value={value}>{ui}</BookContext.Provider>)
  return value
}
