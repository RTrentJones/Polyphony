import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import SceneEditorPage from '@/app/books/editor/page'
import type { RevisionProposal } from '@/lib/revise-api'
import { sha256Hex } from '@/lib/text-offsets'
import type { SceneDetail } from '@/lib/types'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams('scene=s1&book=b1'),
}))

const booksApi = vi.hoisted(() => ({
  getScene: vi.fn(),
  getSceneRevisions: vi.fn(),
  saveSceneContent: vi.fn(),
}))
vi.mock('@/lib/books-store', () => ({ booksApi }))
vi.mock('@/components/SceneGrounding', () => ({ default: () => null }))

const reviseApi = vi.hoisted(() => ({
  revise: vi.fn(),
  proposals: vi.fn(),
  accept: vi.fn(),
  discard: vi.fn(),
}))
vi.mock('@/lib/revise-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/revise-api')>()),
  reviseApi,
}))

const getJob = vi.hoisted(() => vi.fn())
vi.mock('@/lib/jobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/jobs')>()),
  getJob,
}))

const sceneGrounding = vi.hoisted(() => vi.fn())
vi.mock('@/lib/voice-api', () => ({ voiceApi: { sceneGrounding } }))

// An emoji (2 UTF-16 units, 1 code point) and an em dash before the selection;
// the editor text also has a trailing newline, which is part of the raw draft.
const DRAFT = 'Ada 😀 — Zara smiled warmly at him. Then she left.'
const EDITOR_TEXT = `${DRAFT}\n`
const REVISED = 'Ada 😀 — Zara stared coldly at him. Then she left.\n'

const scene: SceneDetail = {
  id: 's1',
  source_id: null,
  characters: ['Zara'],
  status: 'completed',
  created_at: null,
  content: EDITOR_TEXT,
  title: null,
  chapter: null,
  neighbours: { prev: null, next: null },
}

const proposal: RevisionProposal = {
  id: 'rev-1',
  scene_id: 's1',
  content: REVISED,
  word_count: 10,
  created_at: null,
  instruction: 'Make Zara colder',
  range: [8, 34],
  revised_range: [8, 33],
  beat_index: null,
  job_id: 'job-1',
  status: 'proposed',
  warnings: [],
  stale: false,
}

async function renderEditor() {
  render(<SceneEditorPage />)
  return (await screen.findByPlaceholderText('Write your scene...')) as HTMLTextAreaElement
}

function select(editor: HTMLTextAreaElement, phrase: string) {
  const at = editor.value.indexOf(phrase)
  editor.setSelectionRange(at, at + phrase.length)
}

async function requestRevision(editor: HTMLTextAreaElement) {
  select(editor, 'Zara smiled warmly at him.')
  fireEvent.click(screen.getByRole('button', { name: 'Revise selection…' }))
  fireEvent.change(screen.getByLabelText('What should change?'), {
    target: { value: 'Make Zara colder' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Ask for a revision' }))
}

describe('Revise from the scene editor (SPEC-25, W2-3)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    booksApi.getScene.mockReset().mockResolvedValue(scene)
    booksApi.getSceneRevisions
      .mockReset()
      .mockResolvedValue({ scene_id: 's1', revisions: [] })
    booksApi.saveSceneContent.mockReset()
    reviseApi.revise.mockReset().mockResolvedValue({
      scene_id: 's1',
      job_id: 'job-1',
      status: 'processing',
      deduplicated: false,
    })
    reviseApi.proposals
      .mockReset()
      .mockResolvedValueOnce({ scene_id: 's1', draft: EDITOR_TEXT, draft_hash: 'h', proposals: [] })
      .mockResolvedValue({ scene_id: 's1', draft: EDITOR_TEXT, draft_hash: 'h', proposals: [proposal] })
    reviseApi.accept.mockReset()
    reviseApi.discard.mockReset()
    getJob.mockReset().mockResolvedValue({ id: 'job-1', status: 'succeeded', error: null })
    sceneGrounding.mockReset().mockResolvedValue({
      scene_id: 's1',
      mode: 'prose',
      recorded: true,
      beats: [
        { beat_index: 0, description: 'Zara at the desk', characters: [] },
        { beat_index: 1, description: 'She leaves', characters: [] },
      ],
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('sends code-point offsets, the selected text and the draft hash', async () => {
    const editor = await renderEditor()
    await requestRevision(editor)

    await waitFor(() => expect(reviseApi.revise).toHaveBeenCalledTimes(1))
    const [sceneId, request] = reviseApi.revise.mock.calls[0]
    expect(sceneId).toBe('s1')
    // "Zara" is UTF-16 index 9 but code point 8 (the emoji is one code point).
    expect(request.range).toEqual({ start: 8, end: 34 })
    expect(Array.from(DRAFT).slice(8, 34).join('')).toBe('Zara smiled warmly at him.')
    expect(request.selected_text).toBe('Zara smiled warmly at him.')
    expect(request.base_text_hash).toBe(await sha256Hex(EDITOR_TEXT))
    expect(request.instruction).toBe('Make Zara colder')
    expect(request.beat_index).toBeUndefined()
  })

  it('shows the proposal as a word diff once the job succeeds', async () => {
    const editor = await renderEditor()
    await requestRevision(editor)

    const review = await screen.findByTestId('revise-review')
    expect(within(review).getByTestId('diff-delete')).toHaveTextContent('smiled warmly')
    expect(within(review).getByTestId('diff-insert')).toHaveTextContent('stared coldly')
    expect(getJob).toHaveBeenCalledWith('job-1')
  })

  it('Discard leaves the editor text byte-identical', async () => {
    reviseApi.discard.mockResolvedValue({ id: 's1', revision_id: 'rev-1', status: 'discarded' })
    const editor = await renderEditor()
    await requestRevision(editor)
    await screen.findByTestId('revise-review')

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(screen.queryByTestId('revise-review')).toBeNull())
    expect(reviseApi.discard).toHaveBeenCalledWith('s1', 'rev-1')
    expect(reviseApi.accept).not.toHaveBeenCalled()
    expect(editor.value).toBe(EDITOR_TEXT)
    expect(screen.queryByText(/unsaved changes/)).toBeNull()
  })

  it('Accept calls /accept once, takes the proposal as saved text and refreshes revisions', async () => {
    reviseApi.accept.mockResolvedValue({
      id: 's1',
      revision_id: 'rev-1',
      status: 'accepted',
      word_count: 10,
      updated_at: new Date().toISOString(),
    })
    const editor = await renderEditor()
    await requestRevision(editor)
    await screen.findByTestId('revise-review')
    booksApi.getSceneRevisions.mockResolvedValue({
      scene_id: 's1',
      revisions: [{ id: 'old', word_count: 10, source: 'generated', created_at: null, content: DRAFT }],
    })

    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(editor.value).toBe(REVISED))
    expect(reviseApi.accept).toHaveBeenCalledTimes(1)
    expect(reviseApi.accept).toHaveBeenCalledWith('s1', 'rev-1')
    // The server saved it: not dirty, and the snapshot shows up in Revisions.
    expect(screen.queryByText(/unsaved changes/)).toBeNull()
    expect(await screen.findByText('Revision 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('renders the server message for a stale base (409)', async () => {
    reviseApi.revise.mockRejectedValueOnce({
      status: 409,
      message: 'The draft changed since you loaded it; reload and retry.',
    })
    const editor = await renderEditor()
    await requestRevision(editor)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The draft changed since you loaded it; reload and retry.')
    expect(alert).toHaveTextContent('The scene changed; reselect and try again.')
    expect(getJob).not.toHaveBeenCalled()
  })

  it('"Redo beat N" lists the beats and sends the beat index', async () => {
    await renderEditor()
    fireEvent.click(await screen.findByRole('button', { name: 'Redo beat 2' }))
    fireEvent.change(screen.getByLabelText('What should change?'), {
      target: { value: 'Slower exit' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Ask for a revision' }))
    await waitFor(() => expect(reviseApi.revise).toHaveBeenCalledTimes(1))
    const request = reviseApi.revise.mock.calls[0][1]
    expect(request.beat_index).toBe(1)
    expect(request.range).toBeUndefined()
    expect(request.base_text_hash).toBe(await sha256Hex(EDITOR_TEXT))
  })

  it('asks for a selection, and refuses to revise unsaved edits', async () => {
    const editor = await renderEditor()
    editor.setSelectionRange(0, 0)
    fireEvent.click(screen.getByRole('button', { name: 'Revise selection…' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Select the words to revise/)

    fireEvent.change(editor, { target: { value: 'Something new.' } })
    expect(screen.getByText(/Save your edits first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Revise selection…' })).toBeDisabled()
  })

  it('offers a pending proposal again after a reload', async () => {
    reviseApi.proposals.mockReset().mockResolvedValue({
      scene_id: 's1',
      draft: DRAFT,
      draft_hash: 'h',
      proposals: [proposal],
    })
    await renderEditor()
    expect(await screen.findByTestId('revise-review')).toBeInTheDocument()
  })
})
