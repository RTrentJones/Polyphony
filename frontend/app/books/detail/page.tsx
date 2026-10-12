/**
 * Book Detail Page — the book dashboard.
 *
 * Sections: chapters (+ scenes, reorder, generate), outline / beat sheet,
 * plot threads, and continuity reports; plus export (md/docx/epub) and an
 * editable title/synopsis header.
 */

'use client'

import { useCallback, useEffect, useRef, useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  ArrowLeft,
  BookMarked,
  Download,
  FileText,
  Film,
  GitBranch,
  Library,
  ListTree,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import AddSourceModal from '@/components/AddSourceModal'
import Button from '@/components/Button'
import CanonPanel from '@/components/CanonPanel'
import ChaptersSection from '@/components/book/ChaptersSection'
import ContinuityPanel from '@/components/book/ContinuityPanel'
import OutlinePanel from '@/components/book/OutlinePanel'
import ScenesPanel from '@/components/book/ScenesPanel'
import SourcesPanel from '@/components/book/SourcesPanel'
import ThreadsPanel from '@/components/book/ThreadsPanel'
import TrashPanel from '@/components/book/TrashPanel'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import { ToastContainer, type ToastType } from '@/components/Toast'
import { booksApi, useBookStore } from '@/lib/books-store'
import type { BookExportFormat } from '@/lib/types'
import { BOOK_STATUS_STYLES } from '@/components/book/styles'
import { rateLimitedPollDelay } from '@/lib/rate-limit'

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

type TabKey =
  | 'chapters'
  | 'scenes'
  | 'sources'
  | 'canon'
  | 'outline'
  | 'threads'
  | 'continuity'
  | 'trash'

const TABS: Array<{ key: TabKey; label: string; icon: typeof BookMarked }> = [
  { key: 'chapters', label: 'Chapters', icon: BookMarked },
  { key: 'scenes', label: 'Scenes', icon: Film },
  { key: 'sources', label: 'Sources', icon: FileText },
  { key: 'canon', label: 'Canon', icon: Library },
  { key: 'outline', label: 'Outline', icon: ListTree },
  { key: 'threads', label: 'Threads', icon: GitBranch },
  { key: 'continuity', label: 'Continuity', icon: ShieldCheck },
  { key: 'trash', label: 'Trash', icon: Trash2 },
]


interface ToastItem {
  id: string
  message: string
  type: ToastType
}


// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function BookDetailContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const bookId = searchParams.get('id') || ''

  const { currentBook, isLoading, error, fetchBook, updateBook, fetchChapter } =
    useBookStore()

  // `?tab=` deep-links a tab (e.g. back from the extraction review to Sources).
  const initialTab = searchParams.get('tab') as TabKey | null
  const [activeTab, setActiveTab] = useState<TabKey>(
    initialTab && TABS.some((t) => t.key === initialTab) ? initialTab : 'chapters'
  )
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [editOpen, setEditOpen] = useState(false)
  const [addSourceOpen, setAddSourceOpen] = useState(false)
  const [editData, setEditData] = useState({
    title: '',
    author: '',
    genre: '',
    synopsis: '',
    status: 'drafting',
  })
  const [savingEdit, setSavingEdit] = useState(false)
  const [exporting, setExporting] = useState<BookExportFormat | null>(null)

  const scenePollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  const addToast = useCallback((message: string, type: ToastType) => {
    setToasts((prev) => [
      ...prev,
      { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, message, type },
    ])
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  useEffect(() => {
    if (bookId) {
      fetchBook(bookId).catch(() => {
        // error surfaced via store state
      })
    }
  }, [bookId, fetchBook])

  // Clear scene pollers on unmount
  useEffect(() => {
    const timers = scenePollTimers.current
    return () => {
      Object.values(timers).forEach(clearTimeout)
    }
  }, [])

  /** Poll a generating scene every 5s until it leaves 'processing'. */
  const pollScene = useCallback(
    (sceneId: string, chapterId: string) => {
      const tick = async () => {
        try {
          const scene = await booksApi.getScene(sceneId)
          // A paused scene is waiting for AI quota and resumes on its own —
          // keep polling (it used to be reported as a failure).
          if (scene.status === 'processing' || scene.status === 'paused') {
            scenePollTimers.current[sceneId] = setTimeout(tick, 5000)
            return
          }
          delete scenePollTimers.current[sceneId]
          await fetchChapter(chapterId)
          if (scene.status === 'completed') {
            addToast('Scene generated', 'success')
          } else {
            addToast('Scene generation failed', 'error')
          }
        } catch (err) {
          // transient error — keep polling (slower when rate-limited)
          scenePollTimers.current[sceneId] = setTimeout(
            tick,
            rateLimitedPollDelay(err) ?? 5000
          )
        }
      }
      scenePollTimers.current[sceneId] = setTimeout(tick, 5000)
    },
    [fetchChapter, addToast]
  )

  const handleSceneGenerated = useCallback(
    (sceneId: string, chapterId: string) => {
      addToast('Scene generation started', 'info')
      fetchChapter(chapterId).catch(() => {
        // the processing row will appear on the next poll
      })
      pollScene(sceneId, chapterId)
    },
    [addToast, fetchChapter, pollScene]
  )

  const openEdit = () => {
    if (!currentBook) return
    setEditData({
      title: currentBook.title,
      author: currentBook.author || '',
      genre: currentBook.genre || '',
      synopsis: currentBook.synopsis || '',
      status: currentBook.status || 'drafting',
    })
    setEditOpen(true)
  }

  const handleSaveEdit = async () => {
    if (!currentBook || !editData.title.trim()) return
    setSavingEdit(true)
    try {
      await updateBook(currentBook.id, {
        title: editData.title.trim(),
        author: editData.author.trim(),
        genre: editData.genre.trim(),
        synopsis: editData.synopsis.trim(),
        status: editData.status as 'drafting' | 'revising' | 'complete',
      })
      setEditOpen(false)
      addToast('Book updated', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to update book', 'error')
    } finally {
      setSavingEdit(false)
    }
  }

  const handleExport = async (format: BookExportFormat) => {
    if (!currentBook) return
    setExporting(format)
    try {
      await booksApi.exportBook(currentBook.id, format, currentBook.title)
    } catch (err: any) {
      addToast(err.message || `Failed to export ${format.toUpperCase()}`, 'error')
    } finally {
      setExporting(null)
    }
  }

  if (isLoading && !currentBook) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading book..." />
      </div>
    )
  }

  if (!bookId || error || !currentBook) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Card className="text-center py-12">
          <BookMarked className="h-16 w-16 text-red-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            Error Loading Book
          </h3>
          <p className="text-gray-600 mb-6">{error || 'Book not found'}</p>
          <Button onClick={() => router.push('/books')}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Books
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <ToastContainer toasts={toasts} removeToast={removeToast} />

      {/* Header */}
      <div className="mb-8">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push('/books')}
          className="mb-4"
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Books
        </Button>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-3xl font-bold text-gray-900">
                {currentBook.title}
              </h1>
              <span
                className={`px-3 py-1 text-sm font-medium rounded-full ${
                  BOOK_STATUS_STYLES[currentBook.status] ||
                  'bg-gray-100 text-gray-700'
                }`}
              >
                {currentBook.status}
              </span>
              <button
                onClick={openEdit}
                className="p-1.5 text-gray-400 hover:text-primary-600"
                aria-label="Edit book details"
              >
                <Pencil className="h-4 w-4" />
              </button>
            </div>
            <p className="text-gray-600 text-sm mb-1">
              {currentBook.genre || 'No genre'}
              {currentBook.author ? ` • by ${currentBook.author}` : ''}
            </p>
            <p className="text-gray-600 max-w-3xl">
              {currentBook.synopsis || (
                <span className="italic text-gray-400">
                  No synopsis yet — add one to enable outline generation
                </span>
              )}
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            {/* Add source material (upload or paste) to THIS book, in place. */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAddSourceOpen(true)}
            >
              <Plus className="h-4 w-4 mr-1" />
              Add source
            </Button>
            {(['md', 'docx', 'epub'] as BookExportFormat[]).map((format) => (
              <Button
                key={format}
                variant="outline"
                size="sm"
                onClick={() => handleExport(format)}
                isLoading={exporting === format}
                disabled={exporting !== null}
              >
                <Download className="h-4 w-4 mr-1" />
                {format.toUpperCase()}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Tabs */}
      {/* Eight tabs outgrow a phone: the strip scrolls on its own, not the page. */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex gap-4 overflow-x-auto">
          {TABS.map((tab) => {
            const Icon = tab.icon
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                aria-current={activeTab === tab.key ? 'page' : undefined}
                className={`inline-flex shrink-0 items-center whitespace-nowrap px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.key
                    ? 'border-primary-600 text-primary-700'
                    : 'border-transparent text-gray-600 hover:text-primary-600'
                }`}
              >
                <Icon className="h-4 w-4 mr-2" />
                {tab.label}
              </button>
            )
          })}
        </nav>
      </div>

      {activeTab === 'chapters' && (
        <ChaptersSection
          bookId={bookId}
          onSceneGenerated={handleSceneGenerated}
          addToast={addToast}
        />
      )}
      {activeTab === 'scenes' && (
        <ScenesPanel
          bookId={bookId}
          chapters={currentBook.chapters}
          addToast={addToast}
        />
      )}
      {activeTab === 'sources' && (
        <SourcesPanel bookId={bookId} addToast={addToast} />
      )}
      {activeTab === 'canon' && (
        <CanonPanel
          bookId={bookId}
          synopsis={currentBook.synopsis}
          onBookChanged={() => fetchBook(bookId)}
          addToast={addToast}
        />
      )}
      {activeTab === 'outline' && (
        <OutlinePanel
          bookId={bookId}
          hasSynopsis={!!currentBook.synopsis}
          addToast={addToast}
        />
      )}
      {activeTab === 'threads' && (
        <ThreadsPanel
          bookId={bookId}
          chapters={currentBook.chapters}
          addToast={addToast}
        />
      )}
      {activeTab === 'continuity' && (
        <ContinuityPanel
          bookId={bookId}
          chapters={currentBook.chapters}
          addToast={addToast}
        />
      )}

      {activeTab === 'trash' && (
        <TrashPanel
          bookId={bookId}
          addToast={addToast}
          onChanged={() => fetchBook(bookId)}
        />
      )}

      {/* Edit book modal */}
      <Modal
        isOpen={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit Book"
        size="md"
      >
        <div className="space-y-4">
          <Input
            label="Title"
            value={editData.title}
            onChange={(e) => setEditData({ ...editData, title: e.target.value })}
            required
          />
          <Input
            label="Author"
            value={editData.author}
            onChange={(e) => setEditData({ ...editData, author: e.target.value })}
          />
          <Input
            label="Genre"
            value={editData.genre}
            onChange={(e) => setEditData({ ...editData, genre: e.target.value })}
          />
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Synopsis
            </label>
            <textarea
              value={editData.synopsis}
              onChange={(e) => setEditData({ ...editData, synopsis: e.target.value })}
              rows={4}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Status
            </label>
            <select
              value={editData.status}
              onChange={(e) => setEditData({ ...editData, status: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="drafting">Drafting</option>
              <option value="revising">Revising</option>
              <option value="complete">Complete</option>
            </select>
          </div>
          <div className="flex items-center space-x-3 pt-2">
            <Button variant="outline" fullWidth onClick={() => setEditOpen(false)}>
              Cancel
            </Button>
            <Button
              fullWidth
              onClick={handleSaveEdit}
              isLoading={savingEdit}
              disabled={!editData.title.trim()}
            >
              Save
            </Button>
          </div>
        </div>
      </Modal>

      {addSourceOpen && (
        <AddSourceModal bookId={bookId} onClose={() => setAddSourceOpen(false)} />
      )}
    </div>
  )
}

export default function BookDetailPage() {
  return (
    <Suspense fallback={null}>
      <BookDetailContent />
    </Suspense>
  )
}
