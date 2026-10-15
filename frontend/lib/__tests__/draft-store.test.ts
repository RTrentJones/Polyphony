import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AUTOSAVE_INTERVAL_MS,
  clearDraft,
  draftKey,
  hashText,
  readDraft,
  useDraftAutosave,
  writeDraft,
  type DraftAutosaveOptions,
} from '@/lib/draft-store'

const draft = {
  text: 'Milo closed the appendices.',
  savedAt: '2026-10-15T14:03:00.000Z',
  baseHash: hashText('original'),
}

describe('draft-store (SPEC-16)', () => {
  beforeEach(() => window.localStorage.clear())
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('round-trips a draft under polyphony:draft:<sceneId>', () => {
    expect(writeDraft('s1', draft)).toBe(true)
    expect(window.localStorage.getItem('polyphony:draft:s1')).not.toBeNull()
    expect(draftKey('s1')).toBe('polyphony:draft:s1')
    expect(readDraft('s1')).toEqual(draft)
    expect(readDraft('s2')).toBeNull()
    clearDraft('s1')
    expect(readDraft('s1')).toBeNull()
  })

  it('treats malformed stored drafts as no draft', () => {
    window.localStorage.setItem(draftKey('s1'), '{not json')
    expect(readDraft('s1')).toBeNull()
    window.localStorage.setItem(draftKey('s1'), JSON.stringify({ text: 3 }))
    expect(readDraft('s1')).toBeNull()
    window.localStorage.setItem(draftKey('s1'), JSON.stringify({ text: 'x', savedAt: 'nope' }))
    expect(readDraft('s1')).toBeNull()
  })

  it('swallows storage errors (blocked or full storage)', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    expect(readDraft('s1')).toBeNull()
    expect(writeDraft('s1', draft)).toBe(false)
    expect(() => clearDraft('s1')).not.toThrow()
  })

  it('fingerprints text stably', () => {
    expect(hashText('abc')).toBe(hashText('abc'))
    expect(hashText('abc')).not.toBe(hashText('abd'))
  })
})

describe('useDraftAutosave (SPEC-16)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  const base: DraftAutosaveOptions = {
    sceneId: 's1',
    text: 'original',
    baseText: 'original',
    enabled: true,
  }

  it('saves a dirty draft within 5 s, even while typing continues', () => {
    const { rerender } = renderHook((props: DraftAutosaveOptions) => useDraftAutosave(props), {
      initialProps: base,
    })
    rerender({ ...base, text: 'original and more' })
    vi.advanceTimersByTime(2000)
    rerender({ ...base, text: 'original and more words' })
    vi.advanceTimersByTime(2000)
    expect(readDraft('s1')).toBeNull()
    vi.advanceTimersByTime(AUTOSAVE_INTERVAL_MS - 4000)
    // ≤5 s after the first unsaved keystroke, with the latest text.
    expect(readDraft('s1')?.text).toBe('original and more words')
    expect(readDraft('s1')?.baseHash).toBe(hashText('original'))
  })

  it('does nothing while clean, and clears the draft when text returns to saved', () => {
    const { rerender } = renderHook((props: DraftAutosaveOptions) => useDraftAutosave(props), {
      initialProps: base,
    })
    vi.advanceTimersByTime(AUTOSAVE_INTERVAL_MS * 2)
    expect(readDraft('s1')).toBeNull()
    rerender({ ...base, text: 'edited' })
    vi.advanceTimersByTime(AUTOSAVE_INTERVAL_MS)
    expect(readDraft('s1')?.text).toBe('edited')
    // e.g. after a successful save, baseText catches up with text
    rerender({ ...base, text: 'edited', baseText: 'edited' })
    expect(readDraft('s1')).toBeNull()
  })

  it('holds autosave while disabled, leaving an existing draft untouched', () => {
    writeDraft('s1', { ...draft, text: 'older draft' })
    const { rerender } = renderHook((props: DraftAutosaveOptions) => useDraftAutosave(props), {
      initialProps: { ...base, enabled: false },
    })
    rerender({ ...base, text: 'new typing', enabled: false })
    vi.advanceTimersByTime(AUTOSAVE_INTERVAL_MS * 2)
    expect(readDraft('s1')?.text).toBe('older draft')
  })

  it('flushes pending text on unmount and on pagehide', () => {
    const { rerender, unmount } = renderHook(
      (props: DraftAutosaveOptions) => useDraftAutosave(props),
      { initialProps: base }
    )
    rerender({ ...base, text: 'typed then left' })
    window.dispatchEvent(new Event('pagehide'))
    expect(readDraft('s1')?.text).toBe('typed then left')
    rerender({ ...base, text: 'typed more then left' })
    unmount()
    expect(readDraft('s1')?.text).toBe('typed more then left')
  })
})
