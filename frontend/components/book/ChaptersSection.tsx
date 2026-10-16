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
  Layers,
  Pencil,
  Plus,
  Trash2,
  Wand2,
} from 'lucide-react'
import Button from '@/components/Button'
import DeleteChapterModal from '@/components/book/DeleteChapterModal'
import GenerateSceneModal, {
  GENERATE_BLOCKER_ID,
  generateBlocker,
} from '@/components/book/GenerateSceneModal'
import PlannedBeatsEditor, { WrittenLink, writtenLabel } from '@/components/book/PlannedBeatsEditor'
import GeneratePlannedScenesButton, {
  plannedScenesMessage,
} from '@/components/book/GeneratePlannedScenesButton'
import RowActionsMenu, { type RowAction } from '@/components/book/RowActionsMenu'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import VersionHistory from '@/components/VersionHistory'
import type { ToastType } from '@/components/Toast'
import { useBookStore } from '@/lib/books-store'
import { useBook } from '@/components/book/BookProvider'
import type { BookChapter } from '@/lib/types'
import { SCENE_STATUS_STYLES } from '@/components/book/styles'
import JobStatusBadge from '@/components/JobStatusBadge'
import { isActiveJob } from '@/lib/jobs'
import { displayChapterTitle } from '@/lib/utils'

/** "chapter 3, Paperwork": two same-title rows never read alike (SPEC-32). */
function rowLabel(chapter: BookChapter): string {
  return `chapter ${chapter.position + 1}, ${displayChapterTitle(chapter.title)}`
}

export default function ChaptersSection({
  onSceneGenerated,
  addToast,
}: {
  onSceneGenerated: (sceneId: string, chapterId: string) => void
  addToast: (message: string, type: ToastType) => void
}) {
  const router = useRouter()
  const {
    bookId,
    book: currentBook,
    chapters,
    refreshBook,
    refreshChapter: fetchChapter,
  } = useBook()
  const {
    chapterDetails,
    createChapter,
    moveChapter,
    moveScene,
    generatePlannedScenes,
  } = useBookStore()

  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [newChapter, setNewChapter] = useState({ title: '', summary: '' })
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [generateFor, setGenerateFor] = useState<BookChapter | null>(null)
  const [deleteFor, setDeleteFor] = useState<BookChapter | null>(null)
  const [historyFor, setHistoryFor] = useState<BookChapter | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // "Generate all planned scenes" started from the phone menu, per chapter.
  const [plannedBusy, setPlannedBusy] = useState<Record<string, boolean>>({})
  // Live job status per scene, from its badge (see ScenesPanel).
  const [liveStatus, setLiveStatus] = useState<Record<string, string>>({})
  const blocker = generateBlocker(currentBook)
  const describedBy = blocker ? GENERATE_BLOCKER_ID : undefined

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

  const onPlannedDone = (
    chapter: BookChapter,
    message: string,
    kind: 'success' | 'info' | 'error'
  ) => {
    addToast(message, kind)
    if (kind !== 'error') {
      setExpanded((prev) => ({ ...prev, [chapter.id]: true }))
    }
  }

  /** The phone menu's "Generate all planned scenes": the same call and
   *  messages as GeneratePlannedScenesButton, which the wider row shows. */
  const handlePlannedFromMenu = async (chapter: BookChapter) => {
    if (plannedBusy[chapter.id]) return
    setPlannedBusy((m) => ({ ...m, [chapter.id]: true }))
    try {
      const r = await generatePlannedScenes(chapter.id)
      onPlannedDone(
        chapter,
        plannedScenesMessage(r),
        r.queued > 0 && !r.already_running ? 'success' : 'info'
      )
    } catch (err: any) {
      onPlannedDone(chapter, err?.message || 'Failed to start the planned scenes', 'error')
    } finally {
      setPlannedBusy((m) => ({ ...m, [chapter.id]: false }))
    }
  }

  /** Secondary row actions, which live in the "⋯" menu below `sm:`. */
  const menuActions = (chapter: BookChapter): RowAction[] => [
    ...(chapter.planned_beats?.length
      ? [
          {
            key: 'planned',
            label: 'Generate all planned scenes',
            icon: <Layers className="h-4 w-4" />,
            onSelect: () => {
              handlePlannedFromMenu(chapter)
            },
            disabled: !!plannedBusy[chapter.id] || !!blocker,
          },
        ]
      : []),
    {
      key: 'history',
      label: 'History',
      icon: <History className="h-4 w-4" />,
      onSelect: () => setHistoryFor(chapter),
    },
    {
      key: 'delete',
      label: 'Delete chapter',
      icon: <Trash2 className="h-4 w-4" />,
      onSelect: () => handleDeleteChapter(chapter),
      danger: true,
    },
  ]

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
      {blocker && chapters.length > 0 && (
        <p id={GENERATE_BLOCKER_ID} className="mb-3 text-sm text-gray-600">
          {blocker}
        </p>
      )}

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
                data-chapter-id={chapter.id}
                data-chapter-position={chapter.position}
                className="border border-gray-200 rounded-lg"
              >
                {/* Below sm: two lines, the title and then its actions, so a
                    phone always shows the title (B4). From sm: one line. */}
                <div
                  className={`flex flex-col gap-2 p-3 bg-gray-50 sm:flex-row sm:items-center ${
                    isOpen ? 'rounded-t-lg' : 'rounded-lg'
                  }`}
                >
                  <div className="flex min-w-0 flex-1 items-start gap-2 sm:items-center">
                    <button
                      onClick={() => toggleExpand(chapter.id)}
                      className="shrink-0 p-1 text-gray-500 hover:text-primary-600"
                      aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${rowLabel(chapter)}`}
                      aria-expanded={isOpen}
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
                      <p
                        className="font-medium text-gray-900 line-clamp-2 break-words"
                        data-testid="chapter-title"
                        title={chapter.title}
                      >
                        {chapter.position + 1}. {displayChapterTitle(chapter.title)}
                      </p>
                      {chapter.summary && (
                        <p className="text-sm text-gray-500 truncate">
                          {chapter.summary}
                        </p>
                      )}
                    </div>
                  </div>

                  <div
                    className="flex shrink-0 flex-wrap items-center justify-end gap-1"
                    data-testid="chapter-actions"
                  >
                    <button
                      onClick={() => handleMoveChapter(chapter, -1)}
                      disabled={chapter.position === 0 || busy}
                      className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                      aria-label={`Move ${rowLabel(chapter)} up`}
                    >
                      <ArrowUp className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleMoveChapter(chapter, 1)}
                      disabled={chapter.position >= chapters.length - 1 || busy}
                      className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                      aria-label={`Move ${rowLabel(chapter)} down`}
                    >
                      <ArrowDown className="h-4 w-4" />
                    </button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setGenerateFor(chapter)}
                      disabled={!!blocker}
                      aria-describedby={describedBy}
                    >
                      <Wand2 className="h-4 w-4 mr-1" />
                      Generate scene
                    </Button>
                    {/* From sm: the secondary actions sit in the row ... */}
                    <div className="hidden items-center gap-1 sm:flex">
                      <GeneratePlannedScenesButton
                        chapter={chapter}
                        blocker={blocker}
                        onDone={(message, kind) => onPlannedDone(chapter, message, kind)}
                      />
                      <button
                        onClick={() => setHistoryFor(chapter)}
                        className="p-1 text-gray-400 hover:text-primary-600"
                        aria-label={`History of ${rowLabel(chapter)}`}
                      >
                        <History className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => handleDeleteChapter(chapter)}
                        className="p-1 text-gray-400 hover:text-red-600"
                        aria-label={`Delete ${rowLabel(chapter)}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                    {/* ... below sm: they move into the "⋯" menu. */}
                    <RowActionsMenu
                      className="sm:hidden"
                      label={`More actions for ${rowLabel(chapter)}`}
                      actions={menuActions(chapter)}
                    />
                  </div>
                </div>

                {isOpen && (
                  <div className="p-3 border-t border-gray-200">
                    {detail && (editing === chapter.id ? (
                      <PlannedBeatsEditor
                        chapter={detail}
                        onCancel={() => setEditing(null)}
                        onSaved={() => {
                          setEditing(null)
                          addToast('Chapter saved', 'success')
                        }}
                      />
                    ) : (
                      <div className="mb-3 text-sm text-gray-700" data-testid="planned-beats-list">
                        <Button variant="outline" size="sm" onClick={() => setEditing(chapter.id)}>
                          <Pencil className="h-3.5 w-3.5 mr-1" />
                          Edit chapter {chapter.number}
                        </Button>
                        {!!detail.planned_beats?.length && (
                          <p className="mt-2 text-gray-600">{writtenLabel(detail)}</p>
                        )}
                        <ol className="list-decimal pl-5">
                          {detail.planned_beats?.map((b) => (
                            <li key={b.node_id}>
                              {b.title} <WrittenLink chapter={detail} nodeId={b.node_id} />
                            </li>
                          ))}
                        </ol>
                      </div>
                    ))}
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
                            data-testid="scene-row"
                            className="flex flex-wrap items-center gap-x-2 gap-y-1 p-2 rounded-lg border border-gray-100 hover:border-primary-200 sm:flex-nowrap sm:gap-3"
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
                            <p className="order-last basis-full min-w-0 text-sm text-gray-600 truncate sm:order-none sm:basis-auto sm:flex-1">
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
          onRestored={() => {
            refreshBook().catch(() => undefined)
            fetchChapter(historyFor.id).catch(() => undefined)
          }}
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
