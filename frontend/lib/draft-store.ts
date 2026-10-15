/**
 * Local scene drafts (SPEC-16, B1) — the editor's safety net between saves.
 *
 * A draft lives in localStorage under `polyphony:draft:<sceneId>` and never
 * touches the API, so autosave cannot bloat `scene_revisions`. Storage can be
 * missing, full or blocked (private mode, quota, disabled cookies): every read
 * and write is wrapped, and a failure simply means no draft.
 */

import { useEffect, useRef } from 'react'

export const DRAFT_KEY_PREFIX = 'polyphony:draft:'
/** Autosave fires at most this often, and no later than this after an edit. */
export const AUTOSAVE_INTERVAL_MS = 5000

export interface SceneDraft {
  /** The editor text when the draft was written. */
  text: string
  /** ISO time the draft was written. */
  savedAt: string
  /** Fingerprint of the server content the draft was edited from. */
  baseHash: string
}

export function draftKey(sceneId: string): string {
  return `${DRAFT_KEY_PREFIX}${sceneId}`
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/** FNV-1a, 32-bit, hex: a cheap "has the saved text changed" fingerprint. */
export function hashText(text: string): string {
  let hash = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

export function readDraft(sceneId: string): SceneDraft | null {
  if (!sceneId) return null
  try {
    const raw = storage()?.getItem(draftKey(sceneId))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (
      !parsed ||
      typeof parsed.text !== 'string' ||
      typeof parsed.savedAt !== 'string' ||
      Number.isNaN(Date.parse(parsed.savedAt))
    ) {
      return null
    }
    return {
      text: parsed.text,
      savedAt: parsed.savedAt,
      baseHash: typeof parsed.baseHash === 'string' ? parsed.baseHash : '',
    }
  } catch {
    return null
  }
}

/** Returns false when the draft could not be stored (storage blocked or full). */
export function writeDraft(sceneId: string, draft: SceneDraft): boolean {
  if (!sceneId) return false
  try {
    const store = storage()
    if (!store) return false
    store.setItem(draftKey(sceneId), JSON.stringify(draft))
    return true
  } catch {
    return false
  }
}

export function clearDraft(sceneId: string): void {
  if (!sceneId) return
  try {
    storage()?.removeItem(draftKey(sceneId))
  } catch {
    // nothing to clear if storage is unavailable
  }
}

export interface DraftAutosaveOptions {
  /**
   * Fixed for the life of the hook: the editor is keyed by scene, so a scene
   * switch remounts it (the unmount flush saves the old scene's text under
   * its own key) rather than re-rendering it with another scene's id.
   */
  sceneId: string
  /** Current editor text. */
  text: string
  /** The server content the editor was loaded with / last saved. */
  baseText: string
  /**
   * False holds autosave entirely (e.g. while an older draft is waiting for
   * Restore/Discard, so it is not overwritten before the author chooses).
   */
  enabled: boolean
}

function persist(cur: DraftAutosaveOptions): void {
  if (!cur.enabled || !cur.sceneId || cur.text === cur.baseText) return
  writeDraft(cur.sceneId, {
    text: cur.text,
    savedAt: new Date().toISOString(),
    baseHash: hashText(cur.baseText),
  })
}

/**
 * Autosave the editor text to a local draft while it differs from the saved
 * text: at most every AUTOSAVE_INTERVAL_MS, and no later than that after the
 * first unsaved keystroke (a throttle, not a pure debounce, so continuous
 * typing is still captured). Pending text is also flushed when the page is
 * hidden/unloaded and when the editor unmounts (in-app navigation).
 */
export function useDraftAutosave({
  sceneId,
  text,
  baseText,
  enabled,
}: DraftAutosaveOptions): void {
  const latest = useRef({ sceneId, text, baseText, enabled })
  latest.current = { sceneId, text, baseText, enabled }
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isDirty = text !== baseText

  useEffect(() => {
    const flush = () => persist(latest.current)
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      if (timer.current) {
        clearTimeout(timer.current)
        timer.current = null
      }
      flush()
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    if (!isDirty) {
      // Back to the saved text (or just saved): nothing worth restoring.
      if (timer.current) {
        clearTimeout(timer.current)
        timer.current = null
      }
      clearDraft(sceneId)
      return
    }
    if (timer.current) return
    timer.current = setTimeout(() => {
      timer.current = null
      persist(latest.current)
    }, AUTOSAVE_INTERVAL_MS)
  }, [sceneId, text, isDirty, enabled])
}
