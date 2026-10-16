import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

import SceneEditorPage from '@/app/books/editor/page'
import { draftKey, hashText, readDraft, writeDraft } from '@/lib/draft-store'
import type { SceneDetail, SceneRevision } from '@/lib/types'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
const nav = vi.hoisted(() => ({ query: 'scene=s1&book=b1' }))
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(nav.query),
}))

const booksApi = vi.hoisted(() => ({
  getScene: vi.fn(),
  getSceneRevisions: vi.fn(),
  saveSceneContent: vi.fn(),
  updateSceneTitle: vi.fn(),
}))
vi.mock('@/lib/books-store', () => ({ booksApi }))
vi.mock('@/components/SceneGrounding', () => ({ default: () => null }))

const ORIGINAL = 'Milo read the appendices.'
const scene: SceneDetail = {
  id: 's1',
  source_id: null,
  characters: ['Milo Voss'],
  status: 'completed',
  created_at: null,
  content: ORIGINAL,
  title: 'Retained',
  chapter: { id: 'c1', number: 1, title: 'Strong Culture Fit' },
  neighbours: { prev: null, next: 's2' },
  name_warnings: [],
}
const revision: SceneRevision = {
  id: 'r1',
  word_count: 3,
  source: 'manual',
  created_at: null,
  content: 'An older take.',
}

async function renderEditor() {
  render(<SceneEditorPage />)
  return (await screen.findByPlaceholderText('Write your scene...')) as HTMLTextAreaElement
}

function type(editor: HTMLTextAreaElement, text: string) {
  fireEvent.change(editor, { target: { value: text } })
}

describe('Scene editor safety net and header (SPEC-16 B1, SPEC-37)', () => {
  let confirmSpy: MockInstance<(message?: string) => boolean>

  beforeEach(() => {
    nav.query = 'scene=s1&book=b1'
    window.localStorage.clear()
    router.push.mockReset()
    router.replace.mockReset()
    booksApi.getScene.mockReset().mockResolvedValue(scene)
    booksApi.getSceneRevisions.mockReset().mockResolvedValue({ scene_id: 's1', revisions: [] })
    booksApi.saveSceneContent.mockReset()
    booksApi.updateSceneTitle.mockReset()
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
  })
  afterEach(() => vi.restoreAllMocks())

  it('does not ask when there is nothing unsaved', async () => {
    await renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(router.push).toHaveBeenCalledWith('/books/detail?id=b1')
  })

  it('asks before "Back to book" with unsaved text; cancelling keeps the text', async () => {
    const editor = await renderEditor()
    type(editor, 'Twenty minutes of revision.')
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(router.push).not.toHaveBeenCalled()
    expect(router.replace).not.toHaveBeenCalled()
    expect(editor.value).toBe('Twenty minutes of revision.')
  })

  it('leaves on confirm, replacing the Back sentinel instead of pushing past it', async () => {
    const editor = await renderEditor()
    type(editor, 'Twenty minutes of revision.')
    confirmSpy.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(router.replace).toHaveBeenCalledWith('/books/detail?id=b1')
    expect(router.push).not.toHaveBeenCalled()
  })

  it('guards in-app links rendered anywhere (e.g. the Navbar)', async () => {
    const nav = document.createElement('a')
    nav.href = '/books'
    nav.textContent = 'Books'
    document.body.appendChild(nav)
    try {
      const editor = await renderEditor()
      type(editor, 'Unsaved.')
      const cancelled = !fireEvent.click(nav)
      expect(confirmSpy).toHaveBeenCalledTimes(1)
      expect(cancelled).toBe(true)
      expect(router.replace).not.toHaveBeenCalled()
      expect(editor.value).toBe('Unsaved.')

      confirmSpy.mockReturnValue(true)
      fireEvent.click(nav)
      expect(router.replace).toHaveBeenCalledWith('/books')
    } finally {
      nav.remove()
    }
  })

  it('asks on tab close / reload while dirty', async () => {
    const editor = await renderEditor()
    const clean = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(clean)
    expect(clean.defaultPrevented).toBe(false)
    type(editor, 'Unsaved.')
    const dirty = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirty)
    expect(dirty.defaultPrevented).toBe(true)
  })

  it('guards browser Back with a sentinel entry, re-arming it on cancel', async () => {
    const pushState = vi.spyOn(window.history, 'pushState')
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    const editor = await renderEditor()
    type(editor, 'Unsaved.')
    expect(pushState).toHaveBeenCalledTimes(1)

    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }))
    })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(pushState).toHaveBeenCalledTimes(2)
    expect(back).not.toHaveBeenCalled()
    expect(editor.value).toBe('Unsaved.')

    confirmSpy.mockReturnValue(true)
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate', { state: null }))
    })
    expect(back).toHaveBeenCalledTimes(1)
  })

  it('saving clears the local draft and the guard', async () => {
    booksApi.saveSceneContent.mockResolvedValue({ updated_at: new Date().toISOString() })
    const editor = await renderEditor()
    type(editor, 'Final words.')
    // Seed a draft as autosave would have.
    writeDraft('s1', {
      text: 'Final words.',
      savedAt: new Date().toISOString(),
      baseHash: hashText(ORIGINAL),
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(booksApi.saveSceneContent).toHaveBeenCalledWith('s1', 'Final words.'))
    await screen.findByText('Scene saved')
    expect(readDraft('s1')).toBeNull()

    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('offers an older local draft on reopen; Restore puts the text back without saving', async () => {
    const savedAt = new Date(2026, 9, 15, 14, 3).toISOString()
    writeDraft('s1', { text: 'The typed text.', savedAt, baseHash: hashText(ORIGINAL) })
    const editor = await renderEditor()
    expect(screen.getByText(/Restore unsaved draft from 14:03/)).toBeInTheDocument()
    expect(editor.value).toBe(ORIGINAL)
    fireEvent.click(screen.getByRole('button', { name: 'Restore draft' }))
    expect(editor.value).toBe('The typed text.')
    expect(screen.queryByText(/Restore unsaved draft/)).not.toBeInTheDocument()
    expect(booksApi.saveSceneContent).not.toHaveBeenCalled()
    // Restored text is unsaved, so the guard is live.
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(confirmSpy).toHaveBeenCalled()
  })

  it('Discard drops the draft and keeps the saved text', async () => {
    writeDraft('s1', {
      text: 'Abandoned.',
      savedAt: new Date().toISOString(),
      baseHash: hashText(ORIGINAL),
    })
    const editor = await renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
    expect(editor.value).toBe(ORIGINAL)
    expect(window.localStorage.getItem(draftKey('s1'))).toBeNull()
  })

  it('drops the previous scene\'s draft offer when only ?scene= changes', async () => {
    writeDraft('s1', {
      text: 'Typed in s1.',
      savedAt: new Date().toISOString(),
      baseHash: hashText(ORIGINAL),
    })
    booksApi.getScene.mockImplementation(async (id: string) =>
      id === 's2' ? { ...scene, id: 's2', content: 'Scene two.' } : scene
    )
    const view = render(<SceneEditorPage />)
    await screen.findByText(/Restore unsaved draft/)
    // Next reuses the page for another scene that has no local draft.
    nav.query = 'scene=s2&book=b1'
    view.rerender(<SceneEditorPage />)
    const editor = (await screen.findByDisplayValue('Scene two.')) as HTMLTextAreaElement
    expect(editor.value).toBe('Scene two.')
    expect(screen.queryByText(/Restore unsaved draft/)).not.toBeInTheDocument()
    expect(readDraft('s1')?.text).toBe('Typed in s1.')
  })

  it('on a ?scene= switch, flushes the old scene\'s text under its own key and offers only the new scene\'s draft', async () => {
    writeDraft('s2', {
      text: 'Older s2 draft.',
      savedAt: new Date().toISOString(),
      baseHash: hashText('Scene two.'),
    })
    booksApi.getScene.mockImplementation(async (id: string) =>
      id === 's2' ? { ...scene, id: 's2', content: 'Scene two.' } : scene
    )
    const view = render(<SceneEditorPage />)
    const first = (await screen.findByDisplayValue(ORIGINAL)) as HTMLTextAreaElement
    // Unsaved text, switched away from before autosave's 5 s timer fires.
    type(first, 'Typed in s1.')
    expect(readDraft('s1')).toBeNull()

    nav.query = 'scene=s2&book=b1'
    view.rerender(<SceneEditorPage />)
    // The old editor's unmount flush saved s1's text under s1, not s2.
    expect(readDraft('s1')?.text).toBe('Typed in s1.')
    expect(readDraft('s2')?.text).toBe('Older s2 draft.')
    // A pagehide mid-switch must not write s1's text under s2 either.
    window.dispatchEvent(new Event('pagehide'))
    expect(readDraft('s2')?.text).toBe('Older s2 draft.')

    const editor = (await screen.findByDisplayValue('Scene two.')) as HTMLTextAreaElement
    // s2's own draft is offered (3 words), not s1's.
    expect(screen.getByText(/Restore unsaved draft/).parentElement).toHaveTextContent('3 words')
    fireEvent.click(screen.getByRole('button', { name: 'Restore draft' }))
    expect(editor.value).toBe('Older s2 draft.')
    expect(readDraft('s1')?.text).toBe('Typed in s1.')
  })

  it('silently drops a draft identical to the saved text', async () => {
    writeDraft('s1', { text: ORIGINAL, savedAt: new Date().toISOString(), baseHash: '' })
    await renderEditor()
    expect(screen.queryByText(/Restore unsaved draft/)).not.toBeInTheDocument()
    expect(readDraft('s1')).toBeNull()
  })

  it('asks before a revision replaces unsaved text', async () => {
    booksApi.getSceneRevisions.mockResolvedValue({ scene_id: 's1', revisions: [revision] })
    const editor = await renderEditor()
    type(editor, 'Unsaved.')
    fireEvent.click(await screen.findByRole('button', { name: /^Revision 1/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(editor.value).toBe('Unsaved.')
  })

  it('names the chapter by its number; Previous is disabled on the first scene', async () => {
    await renderEditor()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Ch 1 · Strong Culture Fit ›'
    )
    expect(screen.getByLabelText('Scene title')).toHaveValue('Retained')
    expect(screen.getByRole('button', { name: 'Previous scene' })).toBeDisabled()
  })

  it('Next goes through the unsaved-changes guard', async () => {
    const editor = await renderEditor()
    type(editor, 'Unsaved.')
    fireEvent.click(screen.getByRole('button', { name: 'Next scene' }))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    expect(router.push).not.toHaveBeenCalled()
    expect(router.replace).not.toHaveBeenCalled()
  })

  it('saves the title on blur, and Next waits for that save', async () => {
    let finish: (v: { id: string; title: string }) => void = () => {}
    booksApi.updateSceneTitle.mockReturnValue(new Promise((r) => (finish = r)))
    await renderEditor()
    const title = screen.getByLabelText('Scene title')
    fireEvent.change(title, { target: { value: 'The Next Desk' } })
    fireEvent.blur(title)
    await waitFor(() => expect(booksApi.updateSceneTitle).toHaveBeenCalledWith('s1', 'The Next Desk'))

    fireEvent.click(screen.getByRole('button', { name: 'Next scene' }))
    await act(async () => {})
    expect(router.push).not.toHaveBeenCalled()
    await act(async () => finish({ id: 's1', title: 'The Next Desk' }))
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/books/editor?scene=s2&book=b1'))
  })

  it('a second title save waits for the first, so the latest edit lands last', async () => {
    let finish: (v: { id: string; title: string }) => void = () => {}
    booksApi.updateSceneTitle
      .mockReturnValueOnce(new Promise((r) => (finish = r)))
      .mockResolvedValueOnce({ id: 's1', title: 'Second' })
    await renderEditor()
    const title = screen.getByLabelText('Scene title')
    fireEvent.change(title, { target: { value: 'First' } })
    fireEvent.blur(title)
    fireEvent.change(title, { target: { value: 'Second' } })
    fireEvent.blur(title)
    await act(async () => {})
    expect(booksApi.updateSceneTitle).toHaveBeenCalledTimes(1)
    await act(async () => finish({ id: 's1', title: 'First' }))
    await waitFor(() => expect(booksApi.updateSceneTitle).toHaveBeenLastCalledWith('s1', 'Second'))
  })

  it('a revert typed while a save is in flight is saved after it', async () => {
    let finish: (v: { id: string; title: string }) => void = () => {}
    booksApi.updateSceneTitle
      .mockReturnValueOnce(new Promise((r) => (finish = r)))
      .mockResolvedValueOnce({ id: 's1', title: 'Retained' })
    await renderEditor()
    const title = screen.getByLabelText('Scene title')
    fireEvent.change(title, { target: { value: 'New' } })
    fireEvent.blur(title)
    fireEvent.change(title, { target: { value: 'Retained' } })
    fireEvent.blur(title)
    await act(async () => finish({ id: 's1', title: 'New' }))
    await waitFor(() => expect(booksApi.updateSceneTitle).toHaveBeenLastCalledWith('s1', 'Retained'))
  })

  it('Back to book also waits for the title save, and stays when it fails', async () => {
    booksApi.updateSceneTitle.mockRejectedValue(new Error('Server error'))
    await renderEditor()
    const title = screen.getByLabelText('Scene title')
    fireEvent.change(title, { target: { value: 'The Next Desk' } })
    fireEvent.blur(title)
    fireEvent.click(screen.getByRole('button', { name: 'Back to book' }))
    await act(async () => {})
    expect(router.push).not.toHaveBeenCalled()
  })

  it('a failed title save keeps the author on the scene', async () => {
    booksApi.updateSceneTitle.mockRejectedValue(new Error('Server error'))
    await renderEditor()
    const title = screen.getByLabelText('Scene title')
    fireEvent.change(title, { target: { value: 'The Next Desk' } })
    fireEvent.blur(title)
    fireEvent.click(screen.getByRole('button', { name: 'Next scene' }))
    await act(async () => {})
    expect(router.push).not.toHaveBeenCalled()
  })
})

describe('Revision preview dialog (SPEC-16, B13)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    booksApi.getScene.mockReset().mockResolvedValue(scene)
    booksApi.getSceneRevisions
      .mockReset()
      .mockResolvedValue({ scene_id: 's1', revisions: [revision] })
  })

  it('is a labelled modal dialog that traps focus, closes on Escape and returns focus', async () => {
    await renderEditor()
    const trigger = await screen.findByRole('button', { name: /^Revision 1/ })
    trigger.focus()
    fireEvent.click(trigger)

    const dialog = screen.getByRole('dialog', { name: 'Revision preview' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const restore = screen.getByRole('button', { name: 'Restore' })
    const close = screen.getByRole('button', { name: 'Close' })
    expect(restore).toHaveFocus()

    // Tab from the last control wraps to the first; Shift+Tab wraps back.
    close.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(restore).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(close).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})

describe('Scene name warnings (SPEC-48)', () => {
  const WARNING = /When it was generated, this scene named people or places not in the canon/

  beforeEach(() => {
    booksApi.getSceneRevisions.mockReset().mockResolvedValue({ scene_id: 's1', revisions: [] })
  })

  it('(F) lists the names stored at generation', async () => {
    booksApi.getScene.mockReset().mockResolvedValue({ ...scene, name_warnings: ['Gary', 'Procurement'] })
    await renderEditor()
    expect(
      screen.getByText(
        'When it was generated, this scene named people or places not in the canon (may be inventions): Gary, Procurement',
      ),
    ).toBeInTheDocument()
  })

  it('(R) shows nothing when there are none', async () => {
    booksApi.getScene.mockReset().mockResolvedValue(scene)
    await renderEditor()
    expect(screen.queryByText(WARNING)).not.toBeInTheDocument()
  })
})
