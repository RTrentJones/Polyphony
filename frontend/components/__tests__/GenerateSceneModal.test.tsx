import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GenerateSceneModal from '@/components/book/GenerateSceneModal'
import type { BookChapter, Character } from '@/lib/types'

const { listCharacters, generateScene, getUsage, est } = vi.hoisted(() => ({
  listCharacters: vi.fn(),
  generateScene: vi.fn(),
  getUsage: vi.fn(async () => ({ models: {} })),
  est: { value: null as unknown },
}))

vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters } }))
vi.mock('@/lib/books-store', () => ({ useBookStore: () => ({ generateScene }) }))
// The estimate's request, as EstimateHint receives it.
const estimated: { character_ids: string[] }[] = []
vi.mock('@/lib/usage-api', async (orig) => ({ ...(await orig<object>()), getUsage }))
vi.mock('@/components/EstimateHint', async () => {
  const { useEffect } = await import('react')
  return {
    default: function EstimateHint({ request, onEstimate }: { request: any; onEstimate?: any }) {
      if (request) estimated.push(request)
      useEffect(() => onEstimate?.(est.value), [onEstimate])
      return null
    },
  }
})

const chapter: BookChapter = { id: 'ch-1', book_id: 'book-1', position: 0, number: 1, title: 'One' }
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
    est.value = null
  })

  it('remembers the picked model per book and counts its generations left (U1)', async () => {
    getUsage.mockResolvedValueOnce({ models: { lite: {}, flash: {} } })
    est.value = { calls: 4, remaining_requests_today: 18, allowed: true }
    listCharacters.mockResolvedValue(cast)
    renderModal()
    fireEvent.click(await screen.findByRole('button', { name: /Ada/ }))
    fireEvent.change(await screen.findByLabelText(/^Model/), { target: { value: 'flash' } })
    expect(screen.getByTestId('generations-left')).toHaveTextContent('4 flash generations left today')
    expect(localStorage.getItem('scene-model:book-1')).toBe('flash')
    expect(estimated.at(-1)).toMatchObject({ model: 'flash' }) // counted on flash's day
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
    summary: 'Ada meets Bram.',
    planned_beats: [
      {
        node_id: 'n-1',
        title: 'Arrival',
        summary: 'Ada reaches the gate',
        character_ids: ['c-ada'],
        secondary_names: [],
        setting: 'The north gate',
        tone: 'wary',
        cast_ids: ['c-bram', 'c-ada'],
      },
      {
        node_id: 'n-2',
        title: 'Refusal',
        summary: 'Bram will not open it',
        character_ids: [],
        secondary_names: [],
        setting: '',
        tone: '',
        cast_ids: ['c-bram'],
      },
    ],
  }

  const fill = () => {
    fireEvent.change(screen.getByPlaceholderText(/dimly lit tavern/), {
      target: { value: 'the gate' },
    })
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

  it('offers each planned beat, showing its setting and tone', async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal({ chapter: promoted })

    const beats = await screen.findByTestId('planned-beats')
    expect(beats).toHaveTextContent('Arrival: Ada reaches the gate')
    expect(beats).toHaveTextContent('Refusal: Bram will not open it')
    expect(screen.getByTestId('beat-meta')).toHaveTextContent('The north gate · wary')
  })

  it('shows no beats when the chapter has none, whatever its summary says', async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal({
      chapter: {
        ...promoted,
        summary: 'Ada meets Bram.\n\nPlanned beats:\n- Arrival: Ada reaches the gate',
        planned_beats: [],
      },
    })
    await screen.findByTestId('cast-picker')
    expect(screen.queryByTestId('planned-beats')).not.toBeInTheDocument()
  })

  it("defaults the tone to the book's ('') and offers no 'neutral'", async () => {
    listCharacters.mockResolvedValue(cast)
    renderModal()
    await screen.findByTestId('cast-picker')
    const tone = screen.getByDisplayValue("Book's tone") as HTMLSelectElement
    expect(tone.value).toBe('')
    const options = Array.from(tone.options).map((o) => o.value)
    expect(options).not.toContain('neutral')
  })

  const pressed = () =>
    Array.from(
      screen.getByTestId('cast-picker').querySelectorAll('button[aria-pressed="true"]')
    ).map((b) => b.textContent)
  const submitted = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await waitFor(() => expect(generateScene).toHaveBeenCalledTimes(1))
    return generateScene.mock.calls[0][1]
  }

  it("picking a beat fills the form and shows its cast_ids; the estimate and the request carry them (S7, S8)", async () => {
    listCharacters.mockResolvedValue(cast)
    generateScene.mockResolvedValue({ scene_id: 's-1' })
    renderModal({ chapter: promoted })

    const beats = await screen.findByTestId('planned-beats')
    await waitFor(() => expect(pressed()).toHaveLength(2)) // the chapter's cast
    fireEvent.click(beats.querySelector('button')!)
    expect(screen.getByPlaceholderText('What happens in this scene...')).toHaveValue(
      'Arrival: Ada reaches the gate'
    )
    expect(screen.getByPlaceholderText(/dimly lit tavern/)).toHaveValue('The north gate')
    // A free-text beat tone that is not in the list is shown, and selected.
    expect((screen.getByDisplayValue('Wary') as HTMLSelectElement).value).toBe('wary')
    fireEvent.click(Array.from(beats.querySelectorAll('button'))[1])
    // The server's cast for the beat, nothing added or dropped on the client.
    expect(pressed()).toEqual(['Bram· point of view for this chapter'])
    await waitFor(() => expect(estimated.at(-1)?.character_ids).toEqual(['c-bram']))
    fill()
    const request = await submitted()
    expect(request.character_ids).toEqual(['c-bram'])
    expect(request.planned_beat_id).toBe('n-2')
    expect(request.pov_character).toBe('Bram')
  })

  it("locks the chapter's POV chip: it cannot be removed and is always sent (R-2)", async () => {
    listCharacters.mockResolvedValue(cast)
    generateScene.mockResolvedValue({ scene_id: 's-1' })
    renderModal({ chapter: promoted })
    await waitFor(() => expect(pressed()).toHaveLength(2))
    const bram = screen.getByRole('button', { name: /^Bram/ })
    expect(bram).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(bram)
    fireEvent.click(screen.getByRole('button', { name: /^Ada/ }))
    expect(pressed()).toEqual(['Bram· point of view for this chapter'])
    fireEvent.change(screen.getByPlaceholderText('What happens in this scene...'), {
      target: { value: 'Bram keeps the gate shut.' },
    })
    fill()
    expect((await submitted()).character_ids).toEqual(['c-bram'])
  })
})

describe('GenerateSceneModal says when the scene runs differently (SPEC-12)', () => {
  beforeEach(() => {
    listCharacters.mockReset()
    generateScene.mockReset()
  })

  it('passes the backend mode_reason to onNotice', async () => {
    listCharacters.mockResolvedValue(cast)
    generateScene.mockResolvedValue({
      scene_id: 's-1',
      chapter_id: 'ch-1',
      position: 0,
      status: 'processing',
      mode_requested: 'ensemble',
      mode_used: 'prose',
      mode_reason: 'Ensemble is not available on the free tier.',
    })
    const onNotice = vi.fn()
    const props = renderModal({ onNotice })

    const picker = await screen.findByTestId('cast-picker')
    fireEvent.click(picker.querySelector('button')!)
    fireEvent.change(screen.getByPlaceholderText('What happens in this scene...'), {
      target: { value: 'Ada argues with Bram at the gate.' },
    })
    fireEvent.change(screen.getByPlaceholderText(/dimly lit tavern/), {
      target: { value: 'the gate' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await waitFor(() =>
      expect(onNotice).toHaveBeenCalledWith('Ensemble is not available on the free tier.')
    )
    expect(props.onGenerated).toHaveBeenCalledWith('s-1', 'ch-1')
  })
})
