import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import CanonPanel from '@/components/CanonPanel'
import { BookContext } from '@/components/book/BookProvider'
import ChaptersSection from '@/components/book/ChaptersSection'
import ScenesPanel from '@/components/book/ScenesPanel'
import type { BookChapter, BookDetail } from '@/lib/types'
import { bookValue, renderWithBook } from './bookContext'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/lib/canon-store', () => ({
  canonApi: { listCharacters: vi.fn(), getCanon: vi.fn().mockResolvedValue({ entries: [], style: null }), createCharacter: vi.fn() },
}))
vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({ chapterDetails: {}, generatePlannedScenes: vi.fn() }),
  booksApi: { listBookScenes: vi.fn().mockResolvedValue([]) },
}))

const beat = { node_id: 'n-1', title: 'Badge', summary: '', character_ids: [], secondary_names: [], setting: '', tone: '', cast_ids: [] }
const paperwork = (id: string, position: number): BookChapter => ({
  id, book_id: 'book-1', position, number: position + 1, title: 'Paperwork', planned_beats: [beat],
})
const chapters = [paperwork('ch-a', 0), paperwork('ch-b', 1)]
const book = (character_count: number, chs = chapters) =>
  ({ id: 'book-1', title: 'B', status: 'draft', chapters: chs, character_count }) as BookDetail

describe('ChaptersSection rows (SPEC-32)', () => {
  it('labels two same-title chapters by position, and tags rows by id and position', () => {
    renderWithBook(
      <ChaptersSection onSceneGenerated={vi.fn()} addToast={vi.fn()} />,
      bookValue({ chapters, book: book(2) })
    )
    for (const verb of ['Delete', 'Expand', 'Move', 'History of', 'More actions for']) {
      const names = screen
        .getAllByRole('button', { name: new RegExp(`^${verb} chapter \\d, Paperwork`) })
        .map((b) => b.getAttribute('aria-label'))
      expect(new Set(names).size).toBe(verb === 'Move' ? 4 : 2)
    }
    expect(screen.getByRole('button', { name: 'Delete chapter 2, Paperwork' })).toBeInTheDocument()
    const rows = screen.getAllByTestId('chapter-row')
    expect(rows[1]).toHaveAttribute('data-chapter-id', 'ch-b')
    expect(rows[1]).toHaveAttribute('data-chapter-position', '1')
  })

  it('disables Generate with its reason when the book has no characters', () => {
    renderWithBook(
      <ChaptersSection onSceneGenerated={vi.fn()} addToast={vi.fn()} />,
      bookValue({ chapters, book: book(0) })
    )
    const reason = screen.getByText('Add characters to cast a scene')
    const row = screen.getAllByTestId('chapter-row')[0]
    for (const name of ['Generate scene', 'Generate all planned scenes']) {
      const button = within(row).getByRole('button', { name })
      expect(button).toBeDisabled()
      expect(button).toHaveAttribute('aria-describedby', reason.id)
    }
  })

  it('enables Generate once the first character is added in Canon (refreshBook)', async () => {
    function Book() {
      const [b, setB] = useState(book(0))
      const refreshBook = vi.fn(async () => (setB(book(1)), b))
      return (
        <BookContext.Provider value={bookValue({ chapters, book: b, refreshBook })}>
          <CanonPanel addToast={vi.fn()} />
          <ChaptersSection onSceneGenerated={vi.fn()} addToast={vi.fn()} />
        </BookContext.Provider>
      )
    }
    render(<Book />)
    const row = screen.getAllByTestId('chapter-row')[0]
    const generate = within(row).getByRole('button', { name: 'Generate scene' })
    expect(generate).toBeDisabled()
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add' }))[0])
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Milo Voss' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(generate).toBeEnabled())
  })

  it('disables the Scenes tab Generate until the book has a chapter', async () => {
    renderWithBook(<ScenesPanel addToast={vi.fn()} />, bookValue({ book: book(3, []) }))
    const button = await screen.findByRole('button', { name: 'Generate scene' })
    expect(button).toBeDisabled()
    const reason = screen.getByText('Add a chapter first')
    expect(button).toHaveAttribute('aria-describedby', reason.id)
  })
})
