/**
 * Sources panel — the book's raw material, editable in place (re-extracts).
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, History, Pencil, Plus, Trash2 } from 'lucide-react'
import AddSourceModal from '@/components/AddSourceModal'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import VersionHistory from '@/components/VersionHistory'
import type { ToastType } from '@/components/Toast'
import apiClient from '@/lib/api-client'
import { useSourceStore } from '@/lib/store'
import type { Source } from '@/lib/types'

export default function SourcesPanel({
  bookId,
  addToast,
}: {
  bookId: string
  addToast: (message: string, type: ToastType) => void
}) {
  const router = useRouter()
  const { sources, fetchSources, editSource, deleteSource } = useSourceStore()

  const [editing, setEditing] = useState<Source | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [loadingContent, setLoadingContent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [historyFor, setHistoryFor] = useState<Source | null>(null)

  useEffect(() => {
    fetchSources(bookId).catch((err: any) =>
      addToast(err.message || 'Failed to load sources', 'error')
    )
  }, [bookId, fetchSources, addToast])

  const bookSources = sources.filter((s) => s.book_id === bookId)

  const openEdit = async (source: Source) => {
    setEditing(source)
    setEditTitle(source.title)
    setEditContent('')
    setLoadingContent(true)
    try {
      const full = await apiClient.getSource(source.id)
      setEditContent(full.content_text || '')
    } catch (err: any) {
      addToast(err.message || 'Failed to load source content', 'error')
    } finally {
      setLoadingContent(false)
    }
  }

  const saveEdit = async () => {
    if (!editing) return
    setSaving(true)
    try {
      const res = await editSource(editing.id, {
        title: editTitle.trim(),
        content_text: editContent,
      })
      setEditing(null)
      if (res.extraction_run_id) {
        // Content changed → re-extracted; review the fresh proposals.
        router.push(`/sources/review?book=${bookId}&run=${res.extraction_run_id}`)
      } else {
        await fetchSources(bookId)
        addToast('Source updated', 'success')
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update source', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (source: Source) => {
    if (!confirm(`Move "${source.title}" to the Trash? Extracted canon is kept.`)) return
    try {
      await deleteSource(source.id)
      addToast('Source moved to the Trash', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to delete source', 'error')
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Sources</h2>
          <p className="text-sm text-gray-500">
            Raw material for this book — edit the text to re-extract canon
          </p>
        </div>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4 mr-1" />
          Add source
        </Button>
      </div>

      {bookSources.length === 0 ? (
        <div className="text-center py-8">
          <FileText className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No sources yet — add a file or paste text to extract characters and
            canon.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {bookSources.map((source) => {
            const pending =
              source.latest_extraction &&
              (source.latest_extraction.status === 'ready' ||
                source.latest_extraction.status === 'pending')
            return (
              <div
                key={source.id}
                className="flex items-start justify-between gap-3 p-3 rounded-lg border border-gray-200"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-medium text-gray-900 truncate">
                      {source.title}
                    </h3>
                    <span className="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded">
                      {source.kind || 'upload'}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mt-0.5">
                    {source.word_count || 0} words
                  </p>
                  {pending && (
                    <p className="text-xs text-primary-700 mt-1">
                      Extraction ready for review
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {pending && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        router.push(
                          `/sources/review?book=${bookId}&run=${source.latest_extraction!.id}`
                        )
                      }
                    >
                      Review
                    </Button>
                  )}
                  <Button variant="outline" size="sm" onClick={() => openEdit(source)}>
                    <Pencil className="h-3.5 w-3.5 mr-1" />
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setHistoryFor(source)}
                    aria-label={`History of ${source.title}`}
                  >
                    <History className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(source)}
                  >
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {historyFor && (
        <VersionHistory
          bookId={bookId}
          entityType="source"
          entityId={historyFor.id}
          label={historyFor.title}
          onClose={() => setHistoryFor(null)}
          onRestored={() => fetchSources(bookId).catch(() => undefined)}
        />
      )}

      {editing && (
        <Modal
          isOpen
          onClose={() => setEditing(null)}
          title={`Edit source — ${editing.title}`}
          size="lg"
        >
          <div className="space-y-4">
            <Input
              label="Title"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Content
              </label>
              {loadingContent ? (
                <Loading text="Loading content…" />
              ) : (
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  rows={14}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                />
              )}
              <p className="text-xs text-gray-500 mt-1">
                Changing the content re-extracts characters and canon for your
                review — nothing is overwritten until you approve.
              </p>
            </div>
            <div className="flex items-center gap-3 pt-2">
              <Button variant="outline" fullWidth onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button
                fullWidth
                onClick={saveEdit}
                isLoading={saving}
                disabled={loadingContent || !editTitle.trim()}
              >
                Save
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {addOpen && (
        <AddSourceModal bookId={bookId} onClose={() => setAddOpen(false)} />
      )}
    </Card>
  )
}
