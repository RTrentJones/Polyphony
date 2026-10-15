/**
 * Scene Editor Page — edit a scene's draft prose with revision history.
 *
 * /books/editor?scene=<sceneId>&book=<bookId>[&q=<continuity quote to select, SPEC-31>]
 *
 * Safety net (SPEC-16): leaving with unsaved changes asks first
 * (useUnsavedChangesGuard), and the text autosaves to a local draft that is
 * offered back on reopen. Autosave never calls the API.
 *
 * AI revision (SPEC-25): RevisePanel asks for a revision of a selection, a
 * beat or the whole saved scene, and shows it as a word diff to accept or
 * discard. Accepting is a server-side save, so the editor is not dirty after.
 */

'use client'

import { useCallback, useEffect, useRef, useState, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { ArrowLeft, FileText, History, RotateCcw, Save } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Loading from '@/components/Loading'
import SceneGrounding from '@/components/SceneGrounding'
import RevisePanel from '@/components/editor/RevisePanel'
import { ToastContainer, type ToastType } from '@/components/Toast'
import { booksApi } from '@/lib/books-store'
import {
  clearDraft,
  hashText,
  readDraft,
  useDraftAutosave,
  type SceneDraft,
} from '@/lib/draft-store'
import { useUnsavedChangesGuard } from '@/lib/useUnsavedChangesGuard'
import { findQuote } from '@/lib/text-offsets'
import type { Scene, SceneRevision } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'

interface ToastItem {
  id: string
  message: string
  type: ToastType
}

function countWords(text: string): number {
  const trimmed = text.trim()
  return trimmed ? trimmed.split(/\s+/).length : 0
}

/** Local wall-clock "HH:MM" for the draft banner. */
function formatClock(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Modal focus handling (B13): focus moves into the dialog when it opens, Tab
 * and Shift+Tab cycle inside it, Escape closes it, and focus returns to the
 * element that opened it.
 */
function useModalDialog(open: boolean, onClose: () => void) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    // The trigger is recorded before the dialog opens; restore to it on close.
    const trigger = returnFocusRef.current
    const dialog = dialogRef.current
    const focusables = () =>
      dialog ? Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)) : []
    ;(focusables()[0] ?? dialog)?.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const items = focusables()
      if (items.length === 0) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (trigger && trigger.isConnected) trigger.focus()
    }
  }, [open])

  return { dialogRef, returnFocusRef }
}

function SceneEditorContent({ sceneId, bookId }: { sceneId: string; bookId: string }) {
  const [scene, setScene] = useState<Scene | null>(null)
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [revisions, setRevisions] = useState<SceneRevision[]>([])
  const [viewedRevision, setViewedRevision] = useState<SceneRevision | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null)
  const [toasts, setToasts] = useState<ToastItem[]>([])
  /** An older local draft that differs from the server text, awaiting Restore/Discard. */
  const [pendingDraft, setPendingDraft] = useState<SceneDraft | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const addToast = useCallback((message: string, type: ToastType) => {
    setToasts((prev) => [
      ...prev,
      { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, message, type },
    ])
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const loadRevisions = useCallback(async () => {
    if (!sceneId) return
    try {
      const data = await booksApi.getSceneRevisions(sceneId)
      setRevisions(data.revisions)
    } catch {
      // revision history is non-critical; the editor still works
    }
  }, [sceneId])

  useEffect(() => {
    if (!sceneId) {
      setError('No scene specified')
      setIsLoading(false)
      return
    }
    const load = async () => {
      setIsLoading(true)
      try {
        const data = await booksApi.getScene(sceneId)
        setScene(data)
        const serverText = data.content || ''
        setContent(serverText)
        setSavedContent(serverText)
        const draft = readDraft(sceneId)
        if (draft && draft.text !== serverText) {
          setPendingDraft(draft)
        } else if (draft) {
          clearDraft(sceneId)
        }
        await loadRevisions()
      } catch (err: any) {
        setError(err.message || 'Failed to load scene')
      } finally {
        setIsLoading(false)
      }
    }
    load()
  }, [sceneId, loadRevisions])

  // Select the ?q= quote once the draft has loaded; a miss does nothing.
  const quote = useSearchParams().get('q') || ''
  useEffect(() => {
    const el = textareaRef.current
    const range = !isLoading && el && quote ? findQuote(el.value, quote) : null
    if (!el || !range) return
    if (!range) return
    el.focus()
    el.setSelectionRange(range[0], range[1])
    el.scrollTop = (el.scrollHeight * range[0]) / el.value.length - el.clientHeight / 2
  }, [isLoading, quote])

  const isDirty = content !== savedContent
  const wordCount = countWords(content)

  const { guardedPush } = useUnsavedChangesGuard(isDirty)
  useDraftAutosave({
    sceneId,
    text: content,
    baseText: savedContent,
    // Hold autosave until the author decides on an older draft, so it is not
    // overwritten before they choose.
    enabled: !isLoading && !pendingDraft,
  })

  const closeRevision = useCallback(() => setViewedRevision(null), [])
  const { dialogRef, returnFocusRef } = useModalDialog(!!viewedRevision, closeRevision)

  const openRevision = (revision: SceneRevision, trigger: HTMLElement) => {
    returnFocusRef.current = trigger
    setViewedRevision(revision)
  }

  const handleRestoreDraft = () => {
    if (!pendingDraft) return
    // Restored text is unsaved: the guard and autosave take over from here.
    setContent(pendingDraft.text)
    setPendingDraft(null)
    addToast('Draft restored — save to keep it', 'info')
  }

  const handleDiscardDraft = () => {
    clearDraft(sceneId)
    setPendingDraft(null)
  }

  const handleSave = async () => {
    if (!content.trim()) {
      addToast('Cannot save empty content', 'warning')
      return
    }
    setSaving(true)
    try {
      const result = await booksApi.saveSceneContent(sceneId, content)
      setSavedContent(content)
      setLastSavedAt(result.updated_at)
      clearDraft(sceneId)
      setPendingDraft(null)
      addToast('Scene saved', 'success')
      await loadRevisions()
    } catch (err: any) {
      addToast(err.message || 'Failed to save scene', 'error')
    } finally {
      setSaving(false)
    }
  }

  /** An accepted AI revision: the server already saved it as the draft. */
  const handleRevisionAccepted = async (accepted: string, updatedAt: string | null) => {
    setContent(accepted)
    setSavedContent(accepted)
    if (updatedAt) setLastSavedAt(updatedAt)
    clearDraft(sceneId)
    setPendingDraft(null)
    await loadRevisions()
  }

  const handleRestore = (revision: SceneRevision) => {
    if (
      isDirty &&
      !window.confirm('Replace your unsaved changes in the editor with this revision?')
    ) {
      return
    }
    setContent(revision.content)
    setViewedRevision(null)
    addToast('Revision loaded into editor — save to keep it', 'info')
  }

  const backHref = bookId ? `/books/detail?id=${bookId}` : '/books'

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading scene..." />
      </div>
    )
  }

  if (error || !scene) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Card className="text-center py-12">
          <FileText className="h-16 w-16 text-red-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            Error Loading Scene
          </h3>
          <p className="text-gray-600 mb-6">{error || 'Scene not found'}</p>
          <Button onClick={() => guardedPush(backHref)}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to book
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <ToastContainer toasts={toasts} removeToast={removeToast} />

      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => guardedPush(backHref)}
            className="mb-2"
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to book
          </Button>
          <h1 className="text-2xl font-bold text-gray-900">Scene Editor</h1>
          <p className="text-sm text-gray-600">
            {scene.status === 'paused'
              ? 'Paused — daily AI quota reached; generation resumes automatically'
              : scene.status === 'processing'
              ? 'This scene is still generating — content may be incomplete'
              : `${wordCount.toLocaleString()} words`}
            {isDirty && <span className="text-yellow-600"> • unsaved changes</span>}
            {!isDirty && lastSavedAt && (
              <span className="text-gray-400">
                {' '}
                • saved {formatRelativeTime(new Date(lastSavedAt))}
              </span>
            )}
          </p>
        </div>
        <Button onClick={handleSave} isLoading={saving} disabled={!isDirty || !content.trim()}>
          <Save className="h-4 w-4 mr-2" />
          Save
        </Button>
      </div>

      {pendingDraft && (
        <div
          role="status"
          className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-900"
        >
          <p>
            <span className="font-medium">
              Restore unsaved draft from {formatClock(pendingDraft.savedAt)}?
            </span>{' '}
            {countWords(pendingDraft.text).toLocaleString()} words, kept on this device.
            {pendingDraft.baseHash && pendingDraft.baseHash !== hashText(savedContent) && (
              <> The saved scene has changed since this draft was written.</>
            )}
          </p>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={handleRestoreDraft}>
              Restore draft
            </Button>
            <Button variant="outline" size="sm" onClick={handleDiscardDraft}>
              Discard draft
            </Button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Editor */}
        <div className="lg:col-span-3">
          <Card padding="sm">
            <textarea
              ref={textareaRef}
              aria-label="Scene text"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Write your scene..."
              className="w-full min-h-[60vh] px-4 py-3 text-gray-900 leading-relaxed border-0 rounded-lg resize-y focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100 text-sm text-gray-500">
              <span>{wordCount.toLocaleString()} words</span>
              <span>
                {scene.characters && scene.characters.length > 0
                  ? scene.characters.join(', ')
                  : ''}
              </span>
            </div>
          </Card>
          <RevisePanel
            sceneId={scene.id}
            content={content}
            isDirty={isDirty}
            canRevise={scene.status !== 'processing' && scene.status !== 'paused'}
            textareaRef={textareaRef}
            onAccepted={handleRevisionAccepted}
            onNotice={addToast}
          />
          {scene.status === 'completed' && <SceneGrounding sceneId={scene.id} />}
        </div>

        {/* Revision history */}
        <div className="lg:col-span-1">
          <Card>
            <h2 className="text-lg font-semibold text-gray-900 mb-3 flex items-center">
              <History className="h-5 w-5 mr-2 text-primary-600" />
              Revisions
            </h2>
            {revisions.length === 0 ? (
              <p className="text-sm text-gray-500">
                No revisions yet — each save snapshots the previous draft
              </p>
            ) : (
              <div className="space-y-2">
                {revisions.map((revision, index) => (
                  <button
                    key={revision.id}
                    onClick={(e) => openRevision(revision, e.currentTarget)}
                    className="w-full text-left p-3 rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors"
                  >
                    <p className="text-sm font-medium text-gray-900">
                      Revision {revisions.length - index}
                      <span className="ml-2 px-1.5 py-0.5 text-xs bg-gray-100 text-gray-600 rounded">
                        {revision.source}
                      </span>
                    </p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {revision.word_count?.toLocaleString() ?? '?'} words
                      {revision.created_at &&
                        ` • ${formatRelativeTime(new Date(revision.created_at))}`}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* Revision viewer */}
      {viewedRevision && (
        <div className="fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-black bg-opacity-50"
            aria-hidden="true"
            onClick={closeRevision}
          />
          <div className="absolute inset-0 overflow-y-auto pointer-events-none">
            <div className="flex min-h-full items-center justify-center p-4">
              <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="revision-preview-title"
                tabIndex={-1}
                className="relative w-full max-w-3xl bg-white rounded-lg shadow-xl pointer-events-auto focus:outline-none"
              >
                <div className="flex items-center justify-between p-4 border-b border-gray-200">
                  <div>
                    <h3 id="revision-preview-title" className="font-semibold text-gray-900">
                      Revision preview
                    </h3>
                    <p className="text-xs text-gray-500">
                      {viewedRevision.word_count?.toLocaleString() ?? '?'} words
                      {viewedRevision.created_at &&
                        ` • ${formatRelativeTime(new Date(viewedRevision.created_at))}`}
                      {` • ${viewedRevision.source}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button size="sm" onClick={() => handleRestore(viewedRevision)}>
                      <RotateCcw className="h-4 w-4 mr-1" />
                      Restore
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={closeRevision}
                    >
                      Close
                    </Button>
                  </div>
                </div>
                <div className="p-4 max-h-[60vh] overflow-y-auto">
                  <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                    {viewedRevision.content}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Keyed by scene: when only `?scene=` changes, the editor remounts instead of
 * being reused, so no state, draft offer or autosave binding carries over from
 * the previous scene (its unsaved text is flushed under its own key on unmount).
 */
function SceneEditorRoute() {
  const searchParams = useSearchParams()
  const sceneId = searchParams.get('scene') || ''
  return (
    <SceneEditorContent
      key={sceneId}
      sceneId={sceneId}
      bookId={searchParams.get('book') || ''}
    />
  )
}

export default function SceneEditorPage() {
  return (
    <Suspense fallback={null}>
      <SceneEditorRoute />
    </Suspense>
  )
}
