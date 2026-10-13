/**
 * Chapters section — the book's chapters, their scenes, reorder and generate.
 */

'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowDown,
  ArrowUp,
  BookMarked,
  ChevronDown,
  ChevronRight,
  History,
  Pencil,
  Plus,
  Trash2,
  Wand2,
} from 'lucide-react'
import Button from '@/components/Button'
import DeleteChapterModal from '@/components/book/DeleteChapterModal'
import GenerateSceneModal from '@/components/book/GenerateSceneModal'
import GeneratePlannedScenesButton from '@/components/book/GeneratePlannedScenesButton'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import VersionHistory from '@/components/VersionHistory'
import type { ToastType } from '@/components/Toast'
import { useBookStore } from '@/lib/books-store'
import type { BookChapter } from '@/lib/types'
import { SCENE_STATUS_STYLES } from '@/components/book/styles'
import JobStatusBadge from '@/components/JobStatusBadge'
import { isActiveJob } from '@/lib/jobs'

export default function ChaptersSection({
  bookId,
  onSceneGenerated,
  addToast,
}: {
  bookId: string
  onSceneGenerated: (sceneId: string, chapterId: string) => void
  addToast: (message: string, type: ToastType) => void
}) {
  const router = useRouter()
  const {
    currentBook,
    chapterDetails,
    createChapter,
    fetchBook,
    fetchChapter,
    moveChapter,
    moveScene,
  } = useBookStore()

  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [newChapter, setNewChapter] = useState({ title: '', summary: '' })
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [generateFor, setGenerateFor] = useState<BookChapter | null>(null)
  const [deleteFor, setDeleteFor] = useState<BookChapter | null>(null)
  const [historyFor, setHistoryFor] = useState<BookChapter | null>(null)
  const [busy, setBusy] = useState(false)
  // Live job status per scene, from its badge (see ScenesPanel).
  const [liveStatus, setLiveStatus] = useState<Record<string, string>>({})

  const chapters = currentBook?.chapters ?? []

  const toggleExpand = async (chapterId: string) => {
    const next = !expanded[chapterId]
    setExpanded((prev) => ({ ...prev, [chapterId]: next }))
    if (next) {
      try {
        await fetchChapter(chapterId)
      } catch (err: any) {
        addToast(err.message || 'Failed to load chapter scenes', 'error')
      }
    }
  }

  const handleAddChapter = async () => {
    if (!newChapter.title.trim()) {
      setAddError('Please give the chapter a title')
      return
    }
    setAddError(null)
    setAdding(true)
    try {
      await createChapter(bookId, {
        title: newChapter.title.trim(),
        ...(newChapter.summary.trim() ? { summary: newChapter.summary.trim() } : {}),
      })
      setAddModalOpen(false)
      setNewChapter({ title: '', summary: '' })
      addToast('Chapter added', 'success')
    } catch (err: any) {
      setAddError(err.message || 'Failed to add chapter')
    } finally {
      setAdding(false)
    }
  }

  const handleMoveChapter = async (chapter: BookChapter, delta: number) => {
    const target = chapter.position + delta
    if (target < 0 || target >= chapters.length || busy) return
    setBusy(true)
    try {
      await moveChapter(bookId, chapter.id, target)
    } catch (err: any) {
      addToast(err.message || 'Failed to reorder chapter', 'error')
    } finally {
      setBusy(false)
    }
  }

  // A chapter with scenes asks where they go (SPEC-04) — see DeleteChapterModal.
  const handleDeleteChapter = (chapter: BookChapter) => setDeleteFor(chapter)

  const handleMoveScene = async (
    chapterId: string,
    sceneId: string,
    currentPos: number,
    delta: number,
    sceneCount: number
  ) => {
    const target = currentPos + delta
    if (target < 0 || target >= sceneCount || busy) return
    setBusy(true)
    try {
      await moveScene(chapterId, sceneId, target)
    } catch (err: any) {
      addToast(err.message || 'Failed to reorder scene', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">Chapters</h2>
        <Button size="sm" onClick={() => setAddModalOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Add Chapter
        </Button>
      </div>

      {chapters.length === 0 ? (
        <div className="text-center py-8">
          <BookMarked className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No chapters yet — add one, or promote outline nodes into chapters
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {chapters.map((chapter) => {
            const detail = chapterDetails[chapter.id]
            const isOpen = !!expanded[chapter.id]
            return (
              <div
                key={chapter.id}
                data-testid="chapter-row"
                className="border border-gray-200 rounded-lg overflow-hidden"
              >
                <div className="flex items-center gap-2 p-3 bg-gray-50">
                  <button
                    onClick={() => toggleExpand(chapter.id)}
                    className="p-1 text-gray-500 hover:text-primary-600"
                    aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${chapter.title}`}
                  >
                    {isOpen ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </button>

                  <div
                    className="flex-1 min-w-0 cursor-pointer"
                    onClick={() => toggleExpand(chapter.id)}
                  >
                    <p className="font-medium text-gray-900 truncate">
                      {chapter.position + 1}. {chapter.title}
                    </p>
                    {chapter.summary && (
                      <p className="text-sm text-gray-500 truncate">
                        {chapter.summary}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => handleMoveChapter(chapter, -1)}
                      disabled={chapter.position === 0 || busy}
                      className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                      aria-label={`Move ${chapter.title} up`}
                    >
                      <ArrowUp className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleMoveChapter(chapter, 1)}
                      disabled={chapter.position >= chapters.length - 1 || busy}
                      className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                      aria-label={`Move ${chapter.title} down`}
                    >
                      <ArrowDown className="h-4 w-4" />
                    </button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setGenerateFor(chapter)}
                    >
                      <Wand2 className="h-4 w-4 mr-1" />
                      Generate scene
                    </Button>
                    <GeneratePlannedScenesButton
                      chapter={chapter}
                      onDone={(message, kind) => {
                        addToast(message, kind)
                        if (kind !== 'error') {
                          setExpanded((prev) => ({ ...prev, [chapter.id]: true }))
                        }
                      }}
                    />
                    <button
                      onClick={() => setHistoryFor(chapter)}
                      className="p-1 text-gray-400 hover:text-primary-600"
                      aria-label={`History of ${chapter.title}`}
                    >
                      <History className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteChapter(chapter)}
                      className="p-1 text-gray-400 hover:text-red-600"
                      aria-label={`Delete ${chapter.title}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {isOpen && (
                  <div className="p-3 border-t border-gray-200">
                    {!detail ? (
                      <Loading size="sm" text="Loading scenes..." />
                    ) : detail.scenes.length === 0 ? (
                      <p className="text-sm text-gray-500 py-2">
                        No scenes yet — use &quot;Generate scene&quot; to draft one
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {detail.scenes.map((scene) => (
                          <div
                            key={scene.id}
                            className="flex items-center gap-3 p-2 rounded-lg border border-gray-100 hover:border-primary-200"
                          >
                            <span className="text-xs text-gray-400 w-6 text-right">
                              {scene.position + 1}
                            </span>
                            {isActiveJob(scene.job?.status) ? (
                              <JobStatusBadge
                                jobId={scene.job?.job_id}
                                initial={scene.job}
                                onSettled={() => {
                                  fetchChapter(chapter.id).catch(() => {})
                                }}
                                onStatus={(st) =>
                                  setLiveStatus((m) => ({ ...m, [scene.id]: st }))
                                }
                              />
                            ) : (
                              <span
                                className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                                  SCENE_STATUS_STYLES[scene.status] ||
                                  'bg-gray-100 text-gray-700'
                                }`}
                              >
                                {scene.status}
                              </span>
                            )}
                            <p className="flex-1 min-w-0 text-sm text-gray-600 truncate">
                              {scene.preview || (
                                <span className="italic text-gray-400">
                                  {scene.status === 'paused' ||
                                  liveStatus[scene.id] === 'paused'
                                    ? 'Paused — waiting for AI quota'
                                    : scene.status === 'processing'
                                    ? 'Generating…'
                                    : 'No content yet'}
                                </span>
                              )}
                            </p>
                            <span className="text-xs text-gray-400 whitespace-nowrap">
                              {scene.word_count
                                ? `${scene.word_count.toLocaleString()} words`
                                : ''}
                            </span>
                            <button
                              onClick={() =>
                                handleMoveScene(
                                  chapter.id,
                                  scene.id,
                                  scene.position,
                                  -1,
                                  detail.scenes.length
                                )
                              }
                              disabled={scene.position === 0 || busy}
                              className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                              aria-label="Move scene up"
                            >
                              <ArrowUp className="h-3.5 w-3.5" />
                            </button>
                            <button
                              onClick={() =>
                                handleMoveScene(
                                  chapter.id,
                                  scene.id,
                                  scene.position,
                                  1,
                                  detail.scenes.length
                                )
                              }
                              disabled={
                                scene.position >= detail.scenes.length - 1 || busy
                              }
                              className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                              aria-label="Move scene down"
                            >
                              <ArrowDown className="h-3.5 w-3.5" />
                            </button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                router.push(
                                  `/books/editor?scene=${scene.id}&book=${bookId}`
                                )
                              }
                            >
                              <Pencil className="h-3.5 w-3.5 mr-1" />
                              Edit
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Add chapter modal */}
      <Modal
        isOpen={addModalOpen}
        onClose={() => {
          setAddModalOpen(false)
          setNewChapter({ title: '', summary: '' })
          setAddError(null)
        }}
        title="Add Chapter"
        size="md"
      >
        <div className="space-y-4">
          {addError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{addError}</p>
            </div>
          )}
          <Input
            label="Title"
            placeholder="Chapter title"
            value={newChapter.title}
            onChange={(e) => setNewChapter({ ...newChapter, title: e.target.value })}
            required
          />
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Summary (optional)
            </label>
            <textarea
              value={newChapter.summary}
              onChange={(e) =>
                setNewChapter({ ...newChapter, summary: e.target.value })
              }
              placeholder="What this chapter covers — used to steer scene generation"
              rows={3}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>
          <div className="flex items-center space-x-3 pt-2">
            <Button
              variant="outline"
              fullWidth
              onClick={() => {
                setAddModalOpen(false)
                setNewChapter({ title: '', summary: '' })
                setAddError(null)
              }}
            >
              Cancel
            </Button>
            <Button
              fullWidth
              onClick={handleAddChapter}
              isLoading={adding}
              disabled={!newChapter.title.trim()}
            >
              Add Chapter
            </Button>
          </div>
        </div>
      </Modal>

      {/* Generate scene modal */}
      {generateFor && (
        <GenerateSceneModal
          bookId={currentBook?.id ?? ''}
          chapter={generateFor}
          onClose={() => setGenerateFor(null)}
          onGenerated={onSceneGenerated}
          onNotice={(m) => addToast(m, 'info')}
        />
      )}
      {historyFor && (
        <VersionHistory
          bookId={bookId}
          entityType="chapter"
          entityId={historyFor.id}
          label={historyFor.title}
          onClose={() => setHistoryFor(null)}
          onRestored={() => fetchBook(bookId).catch(() => undefined)}
        />
      )}
      {deleteFor && (
        <DeleteChapterModal
          bookId={bookId}
          chapter={deleteFor}
          chapters={chapters}
          onClose={() => setDeleteFor(null)}
          onDeleted={(message) => {
            setDeleteFor(null)
            addToast(message, 'success')
          }}
        />
      )}
    </Card>
  )
}
