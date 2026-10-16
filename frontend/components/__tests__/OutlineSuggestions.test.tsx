/** The Outline panel's "Add <name> to cast" chips (SPEC-27, §1.2). */
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BookPlan } from '@/lib/types'
import { bookValue, renderWithBook } from './bookContext'

const { createCharacter, fetchPlans, state } = vi.hoisted(() => ({
  createCharacter: vi.fn(),
  fetchPlans: vi.fn(),
  state: { plans: [] as BookPlan[] },
}))

vi.mock('@/lib/canon-store', () => ({ canonApi: { createCharacter } }))
vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({}),
  usePlanningStore: () => ({
    plans: state.plans,
    fetchPlans,
    generatePlan: vi.fn(),
    promoteNode: vi.fn(),
    promoteAll: vi.fn(),
    confirmPremise: vi.fn(),
  }),
}))

import OutlinePanel from '@/components/book/OutlinePanel'

const PRAVA_SENTENCE = 'Prava, the intake caseworker, processes his file.'

function outline(overrides: Partial<BookPlan> = {}): BookPlan {
  return {
    id: 'plan-1',
    book_id: 'book-1',
    kind: 'outline',
    status: 'ready',
    content: [
      {
        node_id: 'n-1',
        title: 'Intake',
        summary: 'Milo is processed.',
        secondary_names: ['Prava'],
        children: [
          {
            node_id: 'b-1',
            title: 'The queue',
            summary: 'Milo waits.',
            setting: 'Intake Atrium',
            tone: 'bureaucratic dread',
          },
        ],
      },
    ],
    updated_at: null,
    suggested_characters: [
      { name: 'Prava', synopsis_sentence: PRAVA_SENTENCE, introduction: '' },
      { name: 'Old Fen', synopsis_sentence: '', introduction: '' },
    ],
    ...overrides,
  }
}

describe('Outline suggested cast (SPEC-27)', () => {
  beforeEach(() => {
    createCharacter.mockReset()
    fetchPlans.mockReset()
    fetchPlans.mockResolvedValue([])
    state.plans = [outline()]
  })

  it('renders one chip per suggested character', () => {
    renderWithBook(<OutlinePanel addToast={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Add Prava to cast' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Old Fen to cast' })).toBeInTheDocument()
    // A beat's setting and tone are shown under it.
    expect(screen.getByText('Intake Atrium · bureaucratic dread')).toBeInTheDocument()
  })

  it('renders no chips when the response has none', () => {
    state.plans = [outline({ suggested_characters: [] })]
    renderWithBook(<OutlinePanel addToast={vi.fn()} />)
    expect(screen.queryByTestId('suggested-cast')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /to cast$/ })).not.toBeInTheDocument()
  })

  it('renders no chips when the field is absent (an older API)', () => {
    state.plans = [outline({ suggested_characters: undefined })]
    renderWithBook(<OutlinePanel addToast={vi.fn()} />)
    expect(screen.queryByTestId('suggested-cast')).not.toBeInTheDocument()
  })

  it('a click creates a minor character described by the synopsis sentence, then refetches', async () => {
    createCharacter.mockResolvedValue({ id: 'c-prava', name: 'Prava', book_id: 'book-1' })
    const addToast = vi.fn()
    const value = renderWithBook(<OutlinePanel addToast={addToast} />, bookValue())
    fetchPlans.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Add Prava to cast' }))

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Prava added — now castable', 'success')
    )
    expect(createCharacter).toHaveBeenCalledTimes(1)
    expect(createCharacter).toHaveBeenCalledWith('book-1', {
      name: 'Prava',
      role: 'minor',
      description: PRAVA_SENTENCE,
    })
    expect(fetchPlans).toHaveBeenCalledWith('book-1')
    expect(value.refreshCharacters).toHaveBeenCalled()
  })

  it('a failed create shows the error and does not claim success', async () => {
    createCharacter.mockRejectedValue(new Error('A character named Prava already exists'))
    const addToast = vi.fn()
    renderWithBook(<OutlinePanel addToast={addToast} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add Prava to cast' }))

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'A character named Prava already exists',
        'error'
      )
    )
    expect(addToast).not.toHaveBeenCalledWith(expect.anything(), 'success')
  })
})
