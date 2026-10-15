/**
 * Book Detail Page — the book dashboard.
 *
 * Sections: chapters (+ scenes, reorder, generate), outline / beat sheet,
 * plot threads, and continuity reports; plus an export dialog (md/docx/epub,
 * synopsis, empty chapters, chapter range — B9), an editable title/synopsis
 * header, and a getting-started checklist until the book has its first scene
 * (SPEC-27).
 */

'use client'

import { useCallback, useEffect, useRef, useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  ArrowLeft,
  BookMarked,
  ChevronLeft,
  ChevronRight,
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
import { BookProvider, useBook } from '@/components/book/BookProvider'
import ChaptersSection from '@/components/book/ChaptersSection'
import ContinuityPanel from '@/components/book/ContinuityPanel'
import ExportDialog from '@/components/book/ExportDialog'
import GettingStarted, {
  type GettingStartedAction,
} from '@/components/book/GettingStarted'
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
  const searchParams = useSearchParams()
  const bookId = searchParams.get('id') || ''
  return (
    <BookProvider bookId={bookId}>
      <BookDetailView />
    </BookProvider>
  )
}

function BookDetailView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const {
    bookId,
    book: currentBook,
    isLoading,
    error,
    refreshChapter: fetchChapter,
  } = useBook()
  const { updateBook } = useBookStore()

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
  const [exportOpen, setExportOpen] = useState(false)

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
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
              <h1 className="min-w-0 break-words text-3xl font-bold text-gray-900">
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
                <span className="italic text-gray-500">
                  No synopsis yet — add one to enable outline generation
                </span>
              )}
            </p>
          </div>

          {/* Actions */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Add source material (upload or paste) to THIS book, in place. */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAddSourceOpen(true)}
            >
              <Plus className="h-4 w-4 mr-1" />
              Add source
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setExportOpen(true)}
              aria-haspopup="dialog"
            >
              <Download className="h-4 w-4 mr-1" />
              Export
            </Button>
          </div>
        </div>
      </div>

      <GettingStarted
        bookId={bookId}
        refreshKey={`${activeTab}:${currentBook.chapters?.length ?? 0}`}
        onAction={(action: GettingStartedAction) =>
          action === 'add-source' ? setAddSourceOpen(true) : setActiveTab(action)
        }
      />

      {/* Tabs */}
      <BookTabs activeTab={activeTab} onSelect={setActiveTab} />

      {activeTab === 'chapters' && (
        <ChaptersSection
          onSceneGenerated={handleSceneGenerated}
          addToast={addToast}
        />
      )}
      {activeTab === 'scenes' && (
        <ScenesPanel addToast={addToast} />
      )}
      {activeTab === 'sources' && (
        <SourcesPanel addToast={addToast} />
      )}
      {activeTab === 'canon' && (
        <CanonPanel addToast={addToast} />
      )}
      {activeTab === 'outline' && (
        <OutlinePanel addToast={addToast} />
      )}
      {activeTab === 'threads' && (
        <ThreadsPanel addToast={addToast} />
      )}
      {activeTab === 'continuity' && (
        <ContinuityPanel addToast={addToast} />
      )}

      {activeTab === 'trash' && (
        <TrashPanel addToast={addToast} />
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

      <ExportDialog
        isOpen={exportOpen}
        onClose={() => setExportOpen(false)}
        bookId={currentBook.id}
        bookTitle={currentBook.title}
        chapterCount={currentBook.chapters?.length ?? 0}
      />

      {addSourceOpen && (
        <AddSourceModal bookId={bookId} onClose={() => setAddSourceOpen(false)} />
      )}
    </div>
  )
}

/**
 * The tab strip. Eight tabs outgrow a phone, so the strip scrolls on its own
 * (not the page), and says so (B4): an edge fade and a chevron on whichever
 * side has more tabs, and the active tab is scrolled into view when the page
 * opens (e.g. `?tab=trash`) and whenever it changes.
 */
function BookTabs({
  activeTab,
  onSelect,
}: {
  activeTab: TabKey
  onSelect: (key: TabKey) => void
}) {
  const navRef = useRef<HTMLElement>(null)
  const tabRefs = useRef<Partial<Record<TabKey, HTMLButtonElement | null>>>({})
  const [more, setMore] = useState({ left: false, right: false })

  const measure = useCallback(() => {
    const nav = navRef.current
    if (!nav) return
    const left = nav.scrollLeft > 1
    const right = nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 1
    setMore((m) => (m.left === left && m.right === right ? m : { left, right }))
  }, [])

  useEffect(() => {
    tabRefs.current[activeTab]?.scrollIntoView?.({
      inline: 'nearest',
      block: 'nearest',
    })
    measure()
  }, [activeTab, measure])

  useEffect(() => {
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure])

  const scrollBy = (dir: -1 | 1) => {
    const nav = navRef.current
    if (!nav) return
    nav.scrollBy?.({ left: dir * Math.max(nav.clientWidth * 0.7, 120), behavior: 'smooth' })
  }

  return (
    <div className="relative border-b border-gray-200 mb-6" data-testid="book-tabs">
      <nav
        ref={navRef}
        onScroll={measure}
        aria-label="Book sections"
        className="flex gap-4 overflow-x-auto [scrollbar-width:thin]"
      >
        {TABS.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              ref={(el) => {
                tabRefs.current[tab.key] = el
              }}
              onClick={() => onSelect(tab.key)}
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
      {more.left && (
        <div className="pointer-events-none absolute inset-y-0 left-0 flex w-12 items-center bg-gradient-to-r from-gray-50 via-gray-50/90 to-transparent">
          <button
            type="button"
            onClick={() => scrollBy(-1)}
            aria-label="Scroll tabs left"
            data-testid="tabs-more-left"
            className="pointer-events-auto rounded-full p-1 text-gray-500 hover:text-primary-600"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        </div>
      )}
      {more.right && (
        <div className="pointer-events-none absolute inset-y-0 right-0 flex w-12 items-center justify-end bg-gradient-to-l from-gray-50 via-gray-50/90 to-transparent">
          <button
            type="button"
            onClick={() => scrollBy(1)}
            aria-label="Scroll tabs right"
            data-testid="tabs-more-right"
            className="pointer-events-auto rounded-full p-1 text-gray-500 hover:text-primary-600"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
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
