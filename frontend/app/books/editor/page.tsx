/**
 * Scene Editor Page — edit a scene's draft prose with revision history.
 *
 * /books/editor?scene=<sceneId>&book=<bookId>
 */

'use client'

import { useCallback, useEffect, useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, FileText, History, RotateCcw, Save } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Loading from '@/components/Loading'
import { ToastContainer, type ToastType } from '@/components/Toast'
import { booksApi } from '@/lib/books-store'
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

function SceneEditorContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const sceneId = searchParams.get('scene') || ''
  const bookId = searchParams.get('book') || ''

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
        setContent(data.content || '')
        setSavedContent(data.content || '')
        await loadRevisions()
      } catch (err: any) {
        setError(err.message || 'Failed to load scene')
      } finally {
        setIsLoading(false)
      }
    }
    load()
  }, [sceneId, loadRevisions])

  const isDirty = content !== savedContent
  const wordCount = countWords(content)

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
      addToast('Scene saved', 'success')
      await loadRevisions()
    } catch (err: any) {
      addToast(err.message || 'Failed to save scene', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = (revision: SceneRevision) => {
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
          <Button onClick={() => router.push(backHref)}>
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
            onClick={() => router.push(backHref)}
            className="mb-2"
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to book
          </Button>
          <h1 className="text-2xl font-bold text-gray-900">Scene Editor</h1>
          <p className="text-sm text-gray-600">
            {scene.status === 'processing'
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

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Editor */}
        <div className="lg:col-span-3">
          <Card padding="sm">
            <textarea
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
                    onClick={() => setViewedRevision(revision)}
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
            onClick={() => setViewedRevision(null)}
          />
          <div className="absolute inset-0 overflow-y-auto pointer-events-none">
            <div className="flex min-h-full items-center justify-center p-4">
              <div className="relative w-full max-w-3xl bg-white rounded-lg shadow-xl pointer-events-auto">
                <div className="flex items-center justify-between p-4 border-b border-gray-200">
                  <div>
                    <h3 className="font-semibold text-gray-900">Revision preview</h3>
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
                      onClick={() => setViewedRevision(null)}
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

export default function SceneEditorPage() {
  return (
    <Suspense fallback={null}>
      <SceneEditorContent />
    </Suspense>
  )
}
