import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GenerateSceneModal from '@/components/book/GenerateSceneModal'
import type { BookChapter, Character } from '@/lib/types'

const { listCharacters, generateScene } = vi.hoisted(() => ({
  listCharacters: vi.fn(),
  generateScene: vi.fn(),
}))

vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters } }))
vi.mock('@/lib/books-store', () => ({ useBookStore: () => ({ generateScene }) }))

const chapter: BookChapter = { id: 'ch-1', book_id: 'book-1', position: 0, title: 'One' }
const cast: Character[] = [
  { id: 'c-ada', name: 'Ada', role: 'protagonist', book_id: 'book-1' },
  { id: 'c-bram', name: 'Bram', book_id: 'book-1' },
]

function renderModal(overrides: Partial<Parameters<typeof GenerateSceneModal>[0]> = {}) {
  const props = {
    bookId: 'book-1',
    chapter,
    onClose: vi.fn(),
    onGenerated: vi.fn(),
    ...overrides,
  }
  render(<GenerateSceneModal {...props} />)
  return props
}

describe('GenerateSceneModal cast picker', () => {
  beforeEach(() => {
    listCharacters.mockReset()
    generateScene.mockReset()
  })

  it("renders the book's characters", async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal()

    const picker = await screen.findByTestId('cast-picker')
    expect(listCharacters).toHaveBeenCalledWith('book-1')
    expect(picker).toHaveTextContent('Ada')
    expect(picker).toHaveTextContent('protagonist')
    expect(picker).toHaveTextContent('Bram')
    expect(screen.queryByTestId('empty-cast')).not.toBeInTheDocument()
  })

  it('shows the empty-cast message when the book has no characters', async () => {
    listCharacters.mockResolvedValue([])
    renderModal()

    expect(await screen.findByTestId('empty-cast')).toHaveTextContent(
      'This book has no characters yet'
    )
    expect(screen.queryByTestId('cast-picker')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /generate/i })).toBeDisabled()
  })

  it('submits the picked character ids', async () => {
    listCharacters.mockResolvedValue(cast)
    generateScene.mockResolvedValue({ scene_id: 'scene-9' })
    const props = renderModal()

    await screen.findByTestId('cast-picker')
    const ada = screen.getByRole('button', { name: /Ada/ })
    fireEvent.click(ada)
    expect(ada).toHaveAttribute('aria-pressed', 'true')

    fireEvent.change(screen.getByPlaceholderText('What happens in this scene...'), {
      target: { value: 'Ada confronts the harbourmaster.' },
    })
    fireEvent.change(screen.getByPlaceholderText(/dimly lit tavern/), {
      target: { value: 'The docks at dawn' },
    })
    fireEvent.click(screen.getByRole('button', { name: /generate/i }))

    await waitFor(() => expect(generateScene).toHaveBeenCalledTimes(1))
    const [chapterId, request] = generateScene.mock.calls[0]
    expect(chapterId).toBe('ch-1')
    expect(request.character_ids).toEqual(['c-ada'])
    expect(request).not.toHaveProperty('characters')
    expect(request.setting).toBe('The docks at dawn')
    await waitFor(() => expect(props.onGenerated).toHaveBeenCalledWith('scene-9', 'ch-1'))
  })
})

describe('GenerateSceneModal defaults from a promoted chapter (SPEC-10)', () => {
  beforeEach(() => {
    listCharacters.mockReset()
    generateScene.mockReset()
  })

  const promoted: BookChapter = {
    ...chapter,
    plan_node_id: 'node-1',
    character_ids: ['c-ada', 'c-bram', 'c-gone'],
    pov_character_id: 'c-bram',
    summary:
      'Ada meets Bram.\n\nPlanned beats:\n- Arrival: Ada reaches the gate\n- Refusal: Bram will not open it',
  }

  it("pre-selects the chapter's cast and POV, dropping characters no longer in the book", async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal({ chapter: promoted })

    const picker = await screen.findByTestId('cast-picker')
    await waitFor(() =>
      expect(
        picker.querySelector('button[aria-pressed="true"]')
      ).not.toBeNull()
    )
    const pressed = Array.from(
      picker.querySelectorAll('button[aria-pressed="true"]')
    ).map((b) => b.textContent)
    expect(pressed).toHaveLength(2)
    expect(pressed.join(' ')).toContain('Ada')
    expect(pressed.join(' ')).toContain('Bram')
    expect(screen.getByDisplayValue('Bram')).toBeInTheDocument()
  })

  it('offers each planned beat as a one-click description', async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal({ chapter: promoted })

    const beats = await screen.findByTestId('planned-beats')
    fireEvent.click(
      Array.from(beats.querySelectorAll('button')).find((b) =>
        b.textContent?.startsWith('Refusal')
      )!
    )
    expect(
      screen.getByPlaceholderText('What happens in this scene...')
    ).toHaveValue('Refusal: Bram will not open it')
  })
})
