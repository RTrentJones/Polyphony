import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeleteChapterModal from '@/components/book/DeleteChapterModal'
import type { BookChapter } from '@/lib/types'

const { deleteChapter } = vi.hoisted(() => ({ deleteChapter: vi.fn() }))
vi.mock('@/lib/books-store', () => ({ useBookStore: () => ({ deleteChapter }) }))

const one: BookChapter = { id: 'ch-1', book_id: 'b', position: 0, title: 'One' }
const two: BookChapter = { id: 'ch-2', book_id: 'b', position: 1, title: 'Two' }

function renderModal(chapters = [one, two]) {
  const props = {
    bookId: 'b',
    chapter: one,
    chapters,
    onClose: vi.fn(),
    onDeleted: vi.fn(),
  }
  render(<DeleteChapterModal {...props} />)
  return props
}

const conflict = { status: 409, message: 'has scenes', detail: { scene_count: 3 } }

describe('DeleteChapterModal (SPEC-04)', () => {
  beforeEach(() => deleteChapter.mockReset())

  it('deletes an empty chapter straight away', async () => {
    deleteChapter.mockResolvedValue(undefined)
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Delete chapter' }))
    await waitFor(() => expect(props.onDeleted).toHaveBeenCalled())
    expect(deleteChapter).toHaveBeenCalledWith('b', 'ch-1', undefined)
  })

  it('on 409 asks, then moves the scenes to the chosen chapter', async () => {
    deleteChapter.mockRejectedValueOnce(conflict).mockResolvedValueOnce(undefined)
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Delete chapter' }))

    expect(await screen.findByText(/has 3 scenes/)).toBeInTheDocument()
    expect(props.onDeleted).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Move scenes and delete' }))

    await waitFor(() => expect(props.onDeleted).toHaveBeenCalled())
    expect(deleteChapter).toHaveBeenLastCalledWith('b', 'ch-1', { mode: 'move', to: 'ch-2' })
  })

  it('on 409 can delete the scenes instead', async () => {
    deleteChapter.mockRejectedValueOnce(conflict).mockResolvedValueOnce(undefined)
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'Delete chapter' }))
    await screen.findByText(/has 3 scenes/)

    fireEvent.click(screen.getByLabelText('Delete the scenes with the chapter'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete chapter and scenes' }))
    await waitFor(() =>
      expect(deleteChapter).toHaveBeenLastCalledWith('b', 'ch-1', { mode: 'delete' })
    )
  })

  it('offers only deletion when the book has no other chapter', async () => {
    deleteChapter.mockRejectedValueOnce(conflict)
    renderModal([one])
    fireEvent.click(screen.getByRole('button', { name: 'Delete chapter' }))
    await screen.findByText(/has 3 scenes/)
    expect(
      screen.getByRole('button', { name: 'Delete chapter and scenes' })
    ).toBeInTheDocument()
  })
})
