import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GeneratePlannedScenesButton, {
  plannedScenesMessage,
} from '@/components/book/GeneratePlannedScenesButton'
import type { BookChapter } from '@/lib/types'

const { generatePlannedScenes } = vi.hoisted(() => ({
  generatePlannedScenes: vi.fn(),
}))

vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters: vi.fn() } }))
vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({ generatePlannedScenes }),
}))

const promoted: BookChapter = {
  id: 'ch-1',
  book_id: 'b',
  position: 0,
  title: 'Onboarding',
  summary: 'Milo arrives\n\nPlanned beats:\n- Badge photo: he is dead\n- Lunch: Zara explains',
}

describe('GeneratePlannedScenesButton (SPEC-10)', () => {
  beforeEach(() => generatePlannedScenes.mockReset())

  it('is hidden for a chapter with no planned beats', () => {
    const { container } = render(
      <GeneratePlannedScenesButton
        chapter={{ ...promoted, summary: 'Just a summary' }}
        onDone={vi.fn()}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('starts the batch and reports how many scenes will be written', async () => {
    generatePlannedScenes.mockResolvedValue({
      chapter_id: 'ch-1',
      scene_id: 's-1',
      job_id: 'j-1',
      status: 'processing',
      total: 2,
      queued: 2,
      skipped: 0,
    })
    const onDone = vi.fn()
    render(<GeneratePlannedScenesButton chapter={promoted} onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: /Generate all planned scenes/ }))
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith('Writing 2 planned scenes in order.', 'success')
    )
    expect(generatePlannedScenes).toHaveBeenCalledWith('ch-1')
  })

  it('surfaces a refusal (e.g. the preflight estimate) as an error', async () => {
    generatePlannedScenes.mockRejectedValueOnce({ status: 429, message: 'Needs about 90,000 tokens' })
    const onDone = vi.fn()
    render(<GeneratePlannedScenesButton chapter={promoted} onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: /Generate all planned scenes/ }))
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith('Needs about 90,000 tokens', 'error')
    )
  })

  it('words a running batch, a skip and a finished chapter', () => {
    const base = { chapter_id: 'c', scene_id: null, job_id: null, status: 'x', total: 3 }
    expect(plannedScenesMessage({ ...base, queued: 3, already_running: true })).toMatch(
      /already being written/
    )
    expect(plannedScenesMessage({ ...base, queued: 1, skipped: 2 })).toBe(
      'Writing 1 planned scene in order (2 already written, skipped).'
    )
    expect(plannedScenesMessage({ ...base, queued: 0, skipped: 3 })).toMatch(
      /already has a scene/
    )
  })
})
