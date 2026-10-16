import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GeneratePlannedScenesButton, {
  plannedScenesMessage,
} from '@/components/book/GeneratePlannedScenesButton'
import { beatDescription } from '@/components/book/GenerateSceneModal'
import type { BookChapter } from '@/lib/types'

const { generatePlannedScenes } = vi.hoisted(() => ({
  generatePlannedScenes: vi.fn(),
}))

vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters: vi.fn() } }))
vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({ generatePlannedScenes }),
}))

const beat = (title: string, summary = '') => ({
  node_id: `n-${title}`,
  title,
  summary,
  character_ids: [],
  secondary_names: [],
  setting: '',
  tone: '',
  cast_ids: [],
})

const promoted: BookChapter = {
  id: 'ch-1',
  book_id: 'b',
  position: 0,
  number: 1,
  title: 'Onboarding',
  summary: 'Milo arrives',
  planned_beats: [beat('Badge photo', 'he is dead'), beat('Lunch', 'Zara explains')],
}

describe('GeneratePlannedScenesButton (SPEC-10)', () => {
  beforeEach(() => generatePlannedScenes.mockReset())

  it('is hidden for a chapter with no planned beats, whatever its summary says', () => {
    // The server's planned_beats is the only source (SPEC-23): a summary that
    // still reads like the pre-SPEC-23 block plans nothing.
    for (const planned_beats of [undefined, []]) {
      const { container } = render(
        <GeneratePlannedScenesButton
          chapter={{
            ...promoted,
            summary: 'Milo arrives\n\nPlanned beats:\n- Badge photo: he is dead',
            planned_beats,
          }}
          blocker={null}
          onDone={vi.fn()}
        />
      )
      expect(container).toBeEmptyDOMElement()
    }
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
    render(<GeneratePlannedScenesButton chapter={promoted} blocker={null} onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: /Generate all planned scenes/ }))
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith('Writing 2 planned scenes in order.', 'success')
    )
    expect(generatePlannedScenes).toHaveBeenCalledWith('ch-1')
  })

  it('surfaces a refusal (e.g. the preflight estimate) as an error', async () => {
    generatePlannedScenes.mockRejectedValueOnce({ status: 429, message: 'Needs about 90,000 tokens' })
    const onDone = vi.fn()
    render(<GeneratePlannedScenesButton chapter={promoted} blocker={null} onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: /Generate all planned scenes/ }))
    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith('Needs about 90,000 tokens', 'error')
    )
  })

  it('words a running batch, a skip and a finished chapter', () => {
    const base = { chapter_id: 'c', scene_id: null, job_id: null, status: 'x', total: 3, deferred: 0, reason: null }
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

  it("adds the server's reason when part of the batch is deferred (SPEC-34)", () => {
    const reason =
      '3 of 4 scenes fit your budget now; generate again as your 24-hour budget frees up to write the other 1 (written scenes are skipped).'
    const base = { chapter_id: 'c', scene_id: 's', job_id: 'j', status: 'x', total: 4, skipped: 0 }
    expect(plannedScenesMessage({ ...base, queued: 3, deferred: 1, reason })).toBe(
      `Writing 3 planned scenes in order. ${reason}`
    )
    // A full batch reads as before.
    expect(plannedScenesMessage({ ...base, queued: 4, deferred: 0, reason: null })).toBe(
      'Writing 4 planned scenes in order.'
    )
    // A repeat while the batch runs keeps the deferral.
    expect(
      plannedScenesMessage({ ...base, queued: 2, deferred: 1, reason, already_running: true })
    ).toBe(`The planned scenes for this chapter are already being written. ${reason}`)
  })
})

describe('planned beats come from the server (SPEC-23/25)', () => {
  it('describes a beat byte-identically to beat_scene_description', () => {
    expect(beatDescription(beat('Intake', 'Milo is processed'))).toBe(
      'Intake: Milo is processed'
    )
    expect(beatDescription(beat('Lunch'))).toBe('Lunch:')
  })

  it('renders the button for a chapter with planned_beats and an empty summary', () => {
    render(
      <GeneratePlannedScenesButton
        chapter={{ ...promoted, summary: '', planned_beats: [beat('Intake'), beat('Lunch')] }}
        blocker={null}
        onDone={vi.fn()}
      />
    )
    const button = screen.getByRole('button', { name: /Generate all planned scenes/ })
    expect(button).toHaveAttribute('title', 'One scene per planned beat (2), written in order')
  })
})
