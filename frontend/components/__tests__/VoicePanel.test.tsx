import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SceneGrounding from '@/components/SceneGrounding'
import VoicePanel from '@/components/VoicePanel'
import type { VoiceChunk } from '@/lib/types'

const api = vi.hoisted(() => ({
  listChunks: vi.fn(),
  updateChunk: vi.fn(),
  deleteChunk: vi.fn(),
  addSamples: vi.fn(),
  retrieve: vi.fn(),
  testLine: vi.fn(),
  sceneGrounding: vi.fn(),
}))

vi.mock('@/lib/voice-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/voice-api')>()
  return { ...actual, voiceApi: api }
})

function chunk(overrides: Partial<VoiceChunk>): VoiceChunk {
  return {
    id: 'c-1',
    source_id: null,
    chunk_type: 'dialogue',
    text: 'I will not be hurried.',
    source: 'manual',
    word_count: 5,
    created_at: null,
    ...overrides,
  }
}

function renderPanel() {
  render(<VoicePanel characterId="char-1" characterName="Milo" addToast={() => {}} />)
}

describe('VoicePanel', () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset())
    api.listChunks.mockResolvedValue({
      character_id: 'char-1',
      chunks: [
        chunk({}),
        chunk({ id: 'c-2', chunk_type: 'action', text: 'He straightens his tie.', source: 'ch 2', word_count: 4 }),
      ],
      total: 2,
      skip: 0,
      limit: 20,
    })
  })

  it('renders the chunks with type, source and word count', async () => {
    renderPanel()
    const rows = await screen.findAllByTestId('voice-chunk')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('dialogue')
    expect(rows[0]).toHaveTextContent('manual')
    expect(rows[0]).toHaveTextContent('5 words')
    expect(rows[0]).toHaveTextContent('I will not be hurried.')
    expect(rows[1]).toHaveTextContent('ch 2')
    expect(screen.getByText('Voice samples (2)')).toBeInTheDocument()
    expect(api.listChunks).toHaveBeenCalledWith('char-1', { skip: 0, limit: 20 })
  })

  it('pages through a long list', async () => {
    api.listChunks.mockResolvedValue({
      character_id: 'char-1',
      chunks: [chunk({})],
      total: 45,
      skip: 0,
      limit: 20,
    })
    renderPanel()
    await screen.findAllByTestId('voice-chunk')
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(api.listChunks).toHaveBeenLastCalledWith('char-1', { skip: 20, limit: 20 })
    )
  })

  it('editing a chunk calls PATCH with the new text and reloads', async () => {
    api.updateChunk.mockResolvedValue(undefined)
    renderPanel()
    const [first] = await screen.findAllByTestId('voice-chunk')
    fireEvent.click(within(first).getByRole('button', { name: 'Edit sample' }))
    const box = within(first).getByLabelText('Sample text')
    fireEvent.change(box, { target: { value: 'Listen to them.' } })
    fireEvent.click(within(first).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateChunk).toHaveBeenCalledWith('char-1', 'c-1', 'Listen to them.')
    )
    await waitFor(() => expect(api.listChunks).toHaveBeenCalledTimes(2))
  })

  it('deletes a chunk after confirming', async () => {
    api.deleteChunk.mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPanel()
    const [first] = await screen.findAllByTestId('voice-chunk')
    fireEvent.click(within(first).getByRole('button', { name: 'Delete sample' }))
    await waitFor(() => expect(api.deleteChunk).toHaveBeenCalledWith('char-1', 'c-1'))
  })

  it('adds one sample per paragraph', async () => {
    api.addSamples.mockResolvedValue({ id: 'char-1', indexed: 2 })
    renderPanel()
    await screen.findAllByTestId('voice-chunk')
    fireEvent.change(screen.getByLabelText('New samples'), {
      target: { value: 'First line.\n\n  Second line.\n\n' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Add 2 samples/ }))
    await waitFor(() =>
      expect(api.addSamples).toHaveBeenCalledWith(
        'char-1',
        ['First line.', 'Second line.'],
        'dialogue'
      )
    )
  })

  it('retrieval shows ranked scores with the threshold line', async () => {
    api.retrieve.mockResolvedValue({
      character_id: 'char-1',
      query: 'refuses',
      threshold: 0.2,
      results: [
        { id: 'c-1', text: 'Close match.', score: 0.734, chunk_type: 'dialogue', source: '', word_count: 2 },
        { id: 'c-2', text: 'Far match.', score: 0.1, chunk_type: 'action', source: '', word_count: 2 },
      ],
    })
    renderPanel()
    await screen.findAllByTestId('voice-chunk')
    await userEvent.type(screen.getByLabelText('Retrieval query'), 'refuses')
    fireEvent.click(screen.getByRole('button', { name: 'Retrieve' }))
    const hits = await screen.findAllByTestId('retrieval-hit')
    expect(hits[0]).toHaveTextContent('0.734')
    expect(hits[0]).toHaveTextContent('Close match.')
    expect(hits[1]).toHaveTextContent('0.100')
    const line = screen.getByTestId('threshold-line')
    expect(line).toHaveTextContent('threshold 0.20')
    // drawn between the hit that clears it and the one that does not
    const list = line.parentElement as HTMLElement
    const order = Array.from(list.children).map((el) => el.getAttribute('data-testid'))
    expect(order).toEqual(['retrieval-hit', 'threshold-line', 'retrieval-hit'])
    expect(api.retrieve).toHaveBeenCalledWith('char-1', 'refuses')
  })

  it('tests a line and shows what grounded it', async () => {
    api.testLine.mockResolvedValue({
      character: 'Milo',
      dialogue: 'Not today.',
      action: 'He folds his arms.',
      confidence_score: 0.81,
      grounding: [{ chunk_id: 'c-1', score: 0.66, text: 'I will not be hurried.' }],
    })
    renderPanel()
    await screen.findAllByTestId('voice-chunk')
    fireEvent.change(screen.getByLabelText('Line prompt'), {
      target: { value: 'Asked to hurry' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate line' }))
    const result = await screen.findByTestId('test-line')
    expect(result).toHaveTextContent('Not today.')
    expect(result).toHaveTextContent('0.660 I will not be hurried.')
  })
})

describe('SceneGrounding', () => {
  it('lists per-character samples with scores and marks deleted ones', async () => {
    api.sceneGrounding.mockResolvedValue({
      scene_id: 's-1',
      mode: 'prose',
      recorded: true,
      beats: [
        {
          beat_index: 0,
          description: 'They meet.',
          characters: [
            {
              character_id: 'char-1',
              name: 'Milo',
              samples: [
                { chunk_id: 'c-1', score: 0.5, deleted: false, text: 'Live sample.', chunk_type: 'dialogue', source: '' },
                { chunk_id: 'c-9', score: 0.3, deleted: true, text: null, chunk_type: null, source: null },
              ],
            },
          ],
        },
      ],
    })
    render(<SceneGrounding sceneId="s-1" />)
    fireEvent.click(screen.getByText('Why does it sound like this?'))
    const details = screen.getByText('Why does it sound like this?').closest('details') as HTMLDetailsElement
    details.open = true
    fireEvent(details, new Event('toggle'))
    const panel = await screen.findByTestId('scene-grounding')
    await waitFor(() => expect(panel).toHaveTextContent('Milo'))
    expect(panel).toHaveTextContent('Beat 1 — They meet.')
    expect(panel).toHaveTextContent('0.500Live sample.')
    expect(panel).toHaveTextContent('sample deleted since generation')
    expect(api.sceneGrounding).toHaveBeenCalledWith('s-1')
  })
})
