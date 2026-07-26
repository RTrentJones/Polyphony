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
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  BookMarked,
  ChevronDown,
  ChevronRight,
  Download,
  GitBranch,
  ListTree,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Wand2,
} from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import { ToastContainer, type ToastType } from '@/components/Toast'
import apiClient from '@/lib/api-client'
import { booksApi, useBookStore, usePlanningStore } from '@/lib/books-store'
import { useSourceStore } from '@/lib/store'
import type {
  BookChapter,
  BookExportFormat,
  Character,
  ChapterSceneRequest,
  ContinuityFinding,
  PlanKind,
  PlanNode,
  PlotThread,
  ThreadEventKind,
} from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

type TabKey = 'chapters' | 'outline' | 'threads' | 'continuity'

const TABS: Array<{ key: TabKey; label: string; icon: typeof BookMarked }> = [
  { key: 'chapters', label: 'Chapters', icon: BookMarked },
  { key: 'outline', label: 'Outline', icon: ListTree },
  { key: 'threads', label: 'Threads', icon: GitBranch },
  { key: 'continuity', label: 'Continuity', icon: ShieldCheck },
]

const BOOK_STATUS_STYLES: Record<string, string> = {
  drafting: 'bg-yellow-100 text-yellow-700',
  revising: 'bg-blue-100 text-blue-700',
  complete: 'bg-green-100 text-green-700',
}

const SCENE_STATUS_STYLES: Record<string, string> = {
  completed: 'bg-green-100 text-green-700',
  processing: 'bg-yellow-100 text-yellow-700',
  failed: 'bg-red-100 text-red-700',
}

const THREAD_STATUS_STYLES: Record<string, string> = {
  open: 'bg-blue-100 text-blue-700',
  resolved: 'bg-green-100 text-green-700',
  abandoned: 'bg-gray-100 text-gray-600',
}

const EVENT_KIND_STYLES: Record<string, string> = {
  setup: 'bg-purple-100 text-purple-700',
  development: 'bg-blue-100 text-blue-700',
  payoff: 'bg-green-100 text-green-700',
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 border-red-200',
  major: 'bg-orange-100 text-orange-700 border-orange-200',
  minor: 'bg-gray-100 text-gray-600 border-gray-200',
}

const SEVERITY_ORDER: Record<string, number> = { critical: 0, major: 1, minor: 2 }

const TONES = [
  'neutral',
  'tense',
  'joyful',
  'melancholic',
  'suspenseful',
  'romantic',
] as const

interface ToastItem {
  id: string
  message: string
  type: ToastType
}

// ---------------------------------------------------------------------------
// Generate-scene modal
// ---------------------------------------------------------------------------

function GenerateSceneModal({
  chapter,
  onClose,
  onGenerated,
}: {
  chapter: BookChapter
  onClose: () => void
  onGenerated: (sceneId: string, chapterId: string) => void
}) {
  const { generateScene } = useBookStore()
  const { sources, fetchSources } = useSourceStore()

  const [sourceId, setSourceId] = useState('')
  const [sourceCharacters, setSourceCharacters] = useState<Character[]>([])
  const [loadingCharacters, setLoadingCharacters] = useState(false)
  const [charactersInput, setCharactersInput] = useState('')
  const [description, setDescription] = useState('')
  const [setting, setSetting] = useState('')
  const [tone, setTone] = useState<string>('neutral')
  const [pov, setPov] = useState('')
  const [wordCount, setWordCount] = useState(800)
  const [styleNotes, setStyleNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSources().catch(() => {
      // sources are optional here — comma input still works
    })
  }, [fetchSources])

  useEffect(() => {
    if (!sourceId) {
      setSourceCharacters([])
      return
    }
    let cancelled = false
    setLoadingCharacters(true)
    apiClient
      .getSourceCharacters(sourceId)
      .then((chars) => {
        if (!cancelled) setSourceCharacters(chars)
      })
      .catch(() => {
        if (!cancelled) setSourceCharacters([])
      })
      .finally(() => {
        if (!cancelled) setLoadingCharacters(false)
      })
    return () => {
      cancelled = true
    }
  }, [sourceId])

  const characterNames = charactersInput
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean)

  const toggleCharacter = (name: string) => {
    const next = characterNames.includes(name)
      ? characterNames.filter((c) => c !== name)
      : [...characterNames, name]
    setCharactersInput(next.join(', '))
  }

  const handleSubmit = async () => {
    setError(null)
    if (characterNames.length === 0) {
      setError('Add at least one character')
      return
    }
    if (description.trim().length < 10) {
      setError('Describe the scene in at least 10 characters')
      return
    }
    if (!setting.trim()) {
      setError('Please provide a setting')
      return
    }

    const request: ChapterSceneRequest = {
      ...(sourceId ? { source_id: sourceId } : {}),
      characters: characterNames,
      scene_description: description.trim(),
      setting: setting.trim(),
      emotional_tone: tone,
      ...(pov.trim() ? { pov_character: pov.trim() } : {}),
      target_word_count: Math.min(3000, Math.max(100, wordCount || 800)),
      ...(styleNotes.trim() ? { style_notes: styleNotes.trim() } : {}),
    }

    setSubmitting(true)
    try {
      const result = await generateScene(chapter.id, request)
      onGenerated(result.scene_id, chapter.id)
      onClose()
    } catch (err: any) {
      setError(err.message || 'Failed to start scene generation')
    } finally {
      setSubmitting(false)
    }
  }

  const completedSources = sources.filter(
    (m) => m.processing_status === 'completed'
  )

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={`Generate scene — ${chapter.title}`}
      size="lg"
    >
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}

        {/* Optional character bible from a source */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Character bible (optional)
          </label>
          <select
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          >
            <option value="">No source — use character names only</option>
            {completedSources.map((m) => (
              <option key={m.id} value={m.id}>
                {m.title}
              </option>
            ))}
          </select>
        </div>

        {sourceId &&
          (loadingCharacters ? (
            <Loading size="sm" text="Loading characters..." />
          ) : sourceCharacters.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {sourceCharacters.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => toggleCharacter(c.name)}
                  className={`px-3 py-1 text-sm rounded-full border transition-colors ${
                    characterNames.includes(c.name)
                      ? 'border-primary-500 bg-primary-50 text-primary-700'
                      : 'border-gray-300 text-gray-700 hover:border-primary-300'
                  }`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              No characters found in this source
            </p>
          ))}

        <Input
          label="Characters"
          placeholder="Comma-separated, e.g. Alice, The Warden"
          value={charactersInput}
          onChange={(e) => setCharactersInput(e.target.value)}
          helperText="Who appears in this scene"
          required
        />

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Scene description
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What happens in this scene..."
            rows={3}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>

        <Input
          label="Setting"
          placeholder="e.g., A dimly lit tavern at midnight"
          value={setting}
          onChange={(e) => setSetting(e.target.value)}
          required
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Emotional tone
            </label>
            <select
              value={tone}
              onChange={(e) => setTone(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              {TONES.map((t) => (
                <option key={t} value={t}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <Input
            label="POV character (optional)"
            placeholder="Whose eyes we see through"
            value={pov}
            onChange={(e) => setPov(e.target.value)}
          />
        </div>

        <Input
          label="Target word count"
          type="number"
          min={100}
          max={3000}
          value={wordCount}
          onChange={(e) => setWordCount(parseInt(e.target.value, 10) || 0)}
          helperText="Between 100 and 3000 words"
        />

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Style notes (optional)
          </label>
          <textarea
            value={styleNotes}
            onChange={(e) => setStyleNotes(e.target.value)}
            placeholder="e.g., Terse sentences, present tense, no adverbs"
            rows={2}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>

        <div className="flex items-center space-x-3 pt-4">
          <Button variant="outline" fullWidth onClick={onClose}>
            Cancel
          </Button>
          <Button
            fullWidth
            onClick={handleSubmit}
            isLoading={submitting}
            disabled={
              characterNames.length === 0 ||
              description.trim().length < 10 ||
              !setting.trim()
            }
          >
            <Wand2 className="h-4 w-4 mr-2" />
            Generate
          </Button>
        </div>
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------------------
// Chapters section
// ---------------------------------------------------------------------------

function ChaptersSection({
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
    fetchChapter,
    moveChapter,
    deleteChapter,
    moveScene,
  } = useBookStore()

  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [newChapter, setNewChapter] = useState({ title: '', summary: '' })
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [generateFor, setGenerateFor] = useState<BookChapter | null>(null)
  const [busy, setBusy] = useState(false)

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

  const handleDeleteChapter = async (chapter: BookChapter) => {
    if (!confirm(`Delete chapter "${chapter.title}" and its scenes?`)) return
    try {
      await deleteChapter(bookId, chapter.id)
      addToast('Chapter deleted', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to delete chapter', 'error')
    }
  }

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
                className="border border-gray-200 rounded-lg overflow-hidden"
              >
                <div className="flex items-center gap-2 p-3 bg-gray-50">
                  <button
                    onClick={() => toggleExpand(chapter.id)}
                    className="p-1 text-gray-500 hover:text-primary-600"
                    aria-label={isOpen ? 'Collapse chapter' : 'Expand chapter'}
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
                      aria-label="Move chapter up"
                    >
                      <ArrowUp className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleMoveChapter(chapter, 1)}
                      disabled={chapter.position >= chapters.length - 1 || busy}
                      className="p-1 text-gray-400 hover:text-primary-600 disabled:opacity-30"
                      aria-label="Move chapter down"
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
                    <button
                      onClick={() => handleDeleteChapter(chapter)}
                      className="p-1 text-gray-400 hover:text-red-600"
                      aria-label="Delete chapter"
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
                            <span
                              className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                                SCENE_STATUS_STYLES[scene.status] ||
                                'bg-gray-100 text-gray-700'
                              }`}
                            >
                              {scene.status}
                            </span>
                            <p className="flex-1 min-w-0 text-sm text-gray-600 truncate">
                              {scene.preview || (
                                <span className="italic text-gray-400">
                                  {scene.status === 'processing'
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
          chapter={generateFor}
          onClose={() => setGenerateFor(null)}
          onGenerated={onSceneGenerated}
        />
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Outline panel
// ---------------------------------------------------------------------------

function OutlineNodeView({ node }: { node: PlanNode }) {
  return (
    <div>
      <p className="font-medium text-gray-900">{node.title}</p>
      {node.summary && <p className="text-sm text-gray-600 mt-0.5">{node.summary}</p>}
      {node.children && node.children.length > 0 && (
        <ul className="mt-2 ml-4 space-y-1 border-l-2 border-gray-100 pl-3">
          {node.children.map((child, i) => (
            <li key={i}>
              <p className="text-sm text-gray-800">{child.title}</p>
              {child.summary && (
                <p className="text-xs text-gray-500">{child.summary}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function OutlinePanel({
  bookId,
  hasSynopsis,
  addToast,
}: {
  bookId: string
  hasSynopsis: boolean
  addToast: (message: string, type: ToastType) => void
}) {
  const { plans, fetchPlans, generatePlan, promoteNode } = usePlanningStore()
  const { fetchBook } = useBookStore()

  const [kind, setKind] = useState<PlanKind>('outline')
  const [chaptersTarget, setChaptersTarget] = useState(12)
  const [generating, setGenerating] = useState(false)
  const [promotingIndex, setPromotingIndex] = useState<number | null>(null)

  useEffect(() => {
    fetchPlans(bookId).catch((err: any) => {
      addToast(err.message || 'Failed to load plans', 'error')
    })
  }, [bookId, fetchPlans, addToast])

  const plan = plans.find((p) => p.kind === kind)

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      await generatePlan(bookId, kind, chaptersTarget)
      addToast(`${kind === 'outline' ? 'Outline' : 'Beat sheet'} generated`, 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to generate plan', 'error')
    } finally {
      setGenerating(false)
    }
  }

  const handlePromote = async (index: number, title: string) => {
    setPromotingIndex(index)
    try {
      await promoteNode(bookId, index)
      await fetchBook(bookId)
      addToast(`"${title}" promoted to chapter`, 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to promote node', 'error')
    } finally {
      setPromotingIndex(null)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-4 mb-4">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Outline</h2>
          <p className="text-sm text-gray-500">
            Draft the book&apos;s structure, then promote nodes into chapters
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Kind
            </label>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as PlanKind)}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="outline">Outline</option>
              <option value="beat_sheet">Beat sheet</option>
            </select>
          </div>
          <div className="w-28">
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Chapters
            </label>
            <input
              type="number"
              min={3}
              max={40}
              value={chaptersTarget}
              onChange={(e) => setChaptersTarget(parseInt(e.target.value, 10) || 12)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>
          <Button size="sm" onClick={handleGenerate} isLoading={generating}>
            <Wand2 className="h-4 w-4 mr-1" />
            Generate {kind === 'outline' ? 'outline' : 'beat sheet'}
          </Button>
        </div>
      </div>

      {!hasSynopsis && (
        <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg p-3">
          <p className="text-sm text-yellow-700">
            Add a synopsis to the book (edit the header above) before generating an
            outline.
          </p>
        </div>
      )}

      {!plan || plan.content.length === 0 ? (
        <div className="text-center py-8">
          <ListTree className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No {kind === 'outline' ? 'outline' : 'beat sheet'} yet
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {plan.content.map((node, index) => (
            <div
              key={index}
              className="flex items-start gap-3 p-3 rounded-lg border border-gray-200"
            >
              <span className="text-xs text-gray-400 mt-1 w-6 text-right">
                {index + 1}
              </span>
              <div className="flex-1 min-w-0">
                <OutlineNodeView node={node} />
              </div>
              {kind === 'outline' && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handlePromote(index, node.title)}
                  isLoading={promotingIndex === index}
                  disabled={promotingIndex !== null}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Promote to chapter
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Threads panel
// ---------------------------------------------------------------------------

function ThreadCard({
  bookId,
  thread,
  chapters,
  addToast,
}: {
  bookId: string
  thread: PlotThread
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { updateThread, deleteThread, addThreadEvent } = usePlanningStore()

  const [eventNote, setEventNote] = useState('')
  const [eventKind, setEventKind] = useState<ThreadEventKind>('development')
  const [eventChapterId, setEventChapterId] = useState('')
  const [addingEvent, setAddingEvent] = useState(false)

  const chapterTitle = (id: string | null) =>
    id ? chapters.find((c) => c.id === id)?.title ?? null : null

  const setStatus = async (status: 'open' | 'resolved' | 'abandoned') => {
    try {
      await updateThread(bookId, thread.id, { status })
    } catch (err: any) {
      addToast(err.message || 'Failed to update thread', 'error')
    }
  }

  const handleDelete = async () => {
    if (!confirm(`Delete thread "${thread.name}"?`)) return
    try {
      await deleteThread(bookId, thread.id)
      addToast('Thread deleted', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to delete thread', 'error')
    }
  }

  const handleAddEvent = async () => {
    if (!eventNote.trim()) return
    setAddingEvent(true)
    try {
      await addThreadEvent(bookId, thread.id, {
        note: eventNote.trim(),
        kind: eventKind,
        ...(eventChapterId ? { chapter_id: eventChapterId } : {}),
      })
      setEventNote('')
      setEventChapterId('')
      setEventKind('development')
    } catch (err: any) {
      addToast(err.message || 'Failed to add event', 'error')
    } finally {
      setAddingEvent(false)
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          {thread.color && (
            <span
              className="h-3 w-3 rounded-full shrink-0 border border-gray-200"
              style={{ backgroundColor: thread.color }}
            />
          )}
          <h3 className="font-semibold text-gray-900 truncate">{thread.name}</h3>
          <span
            className={`px-2 py-0.5 text-xs font-medium rounded-full ${
              THREAD_STATUS_STYLES[thread.status] || 'bg-gray-100 text-gray-600'
            }`}
          >
            {thread.status}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {thread.status !== 'resolved' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('resolved')}>
              Resolve
            </Button>
          )}
          {thread.status !== 'open' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('open')}>
              Reopen
            </Button>
          )}
          {thread.status === 'open' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('abandoned')}>
              Abandon
            </Button>
          )}
          <button
            onClick={handleDelete}
            className="p-1 text-gray-400 hover:text-red-600"
            aria-label="Delete thread"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {thread.description && (
        <p className="text-sm text-gray-600 mb-3">{thread.description}</p>
      )}

      {/* Events */}
      {thread.events && thread.events.length > 0 && (
        <ul className="space-y-1.5 mb-3">
          {thread.events.map((event) => (
            <li key={event.id} className="flex items-start gap-2 text-sm">
              <span
                className={`px-2 py-0.5 text-xs font-medium rounded-full shrink-0 ${
                  EVENT_KIND_STYLES[event.kind] || 'bg-gray-100 text-gray-600'
                }`}
              >
                {event.kind}
              </span>
              <span className="text-gray-700">
                {event.note}
                {chapterTitle(event.chapter_id) && (
                  <span className="text-gray-400">
                    {' '}
                    — {chapterTitle(event.chapter_id)}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Add event */}
      <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-gray-100">
        <input
          value={eventNote}
          onChange={(e) => setEventNote(e.target.value)}
          placeholder="Add an event note..."
          className="flex-1 min-w-[10rem] px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <select
          value={eventKind}
          onChange={(e) => setEventKind(e.target.value as ThreadEventKind)}
          className="px-2 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          <option value="setup">Setup</option>
          <option value="development">Development</option>
          <option value="payoff">Payoff</option>
        </select>
        <select
          value={eventChapterId}
          onChange={(e) => setEventChapterId(e.target.value)}
          className="px-2 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 max-w-[12rem]"
        >
          <option value="">No chapter</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.position + 1}. {c.title}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          onClick={handleAddEvent}
          isLoading={addingEvent}
          disabled={!eventNote.trim()}
        >
          <Plus className="h-3.5 w-3.5 mr-1" />
          Add
        </Button>
      </div>
    </div>
  )
}

function ThreadsPanel({
  bookId,
  chapters,
  addToast,
}: {
  bookId: string
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { threads, fetchThreads, createThread } = usePlanningStore()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    fetchThreads(bookId).catch((err: any) => {
      addToast(err.message || 'Failed to load threads', 'error')
    })
  }, [bookId, fetchThreads, addToast])

  const handleCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    try {
      await createThread(bookId, {
        name: name.trim(),
        ...(description.trim() ? { description: description.trim() } : {}),
      })
      setName('')
      setDescription('')
      addToast('Thread created', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to create thread', 'error')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Card>
      <div className="mb-4">
        <h2 className="text-xl font-semibold text-gray-900">Plot Threads</h2>
        <p className="text-sm text-gray-500">
          Track setups, developments, and payoffs across the book
        </p>
      </div>

      {/* Add thread */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Thread name, e.g. The stolen locket"
          className="flex-1 min-w-[12rem] px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          className="flex-1 min-w-[12rem] px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <Button size="sm" onClick={handleCreate} isLoading={creating} disabled={!name.trim()}>
          <Plus className="h-4 w-4 mr-1" />
          Add Thread
        </Button>
      </div>

      {threads.length === 0 ? (
        <div className="text-center py-8">
          <GitBranch className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No plot threads yet</p>
        </div>
      ) : (
        <div className="space-y-4">
          {threads.map((thread) => (
            <ThreadCard
              key={thread.id}
              bookId={bookId}
              thread={thread}
              chapters={chapters}
              addToast={addToast}
            />
          ))}
        </div>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Continuity panel
// ---------------------------------------------------------------------------

function FindingRow({ finding }: { finding: ContinuityFinding }) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        SEVERITY_STYLES[finding.severity] || SEVERITY_STYLES.minor
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-semibold uppercase tracking-wide">
          {finding.severity}
        </span>
        <span className="text-xs opacity-70">{finding.type}</span>
      </div>
      <p className="text-sm">{finding.detail}</p>
      {finding.refs && <p className="text-xs mt-1 opacity-70">Ref: {finding.refs}</p>}
    </div>
  )
}

function ContinuityPanel({
  bookId,
  chapters,
  addToast,
}: {
  bookId: string
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { reports, fetchContinuityReports, runContinuityCheck } = usePlanningStore()

  const [scopeChapterId, setScopeChapterId] = useState('')
  const [running, setRunning] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const schedulePoll = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current)
    pollTimer.current = setTimeout(async () => {
      try {
        const latest = await fetchContinuityReports(bookId)
        if (latest.some((r) => r.status === 'processing')) {
          schedulePoll()
        }
      } catch {
        schedulePoll()
      }
    }, 5000)
  }, [bookId, fetchContinuityReports])

  useEffect(() => {
    fetchContinuityReports(bookId)
      .then((latest) => {
        if (latest.some((r) => r.status === 'processing')) schedulePoll()
      })
      .catch((err: any) => {
        addToast(err.message || 'Failed to load continuity reports', 'error')
      })
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current)
    }
  }, [bookId, fetchContinuityReports, schedulePoll, addToast])

  const handleRun = async () => {
    setRunning(true)
    try {
      await runContinuityCheck(bookId, scopeChapterId || undefined)
      addToast('Continuity check started', 'info')
      schedulePoll()
    } catch (err: any) {
      addToast(err.message || 'Failed to start continuity check', 'error')
    } finally {
      setRunning(false)
    }
  }

  const chapterTitle = (id: string | null) =>
    id ? chapters.find((c) => c.id === id)?.title ?? 'Unknown chapter' : null

  const sortedFindings = (findings: ContinuityFinding[]) =>
    [...findings].sort(
      (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
    )

  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Continuity</h2>
          <p className="text-sm text-gray-500">
            Check the prose for contradictions against the bible, threads, and
            chapter summaries
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Scope
            </label>
            <select
              value={scopeChapterId}
              onChange={(e) => setScopeChapterId(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent max-w-[16rem]"
            >
              <option value="">Whole book</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  Chapter {c.position + 1}: {c.title}
                </option>
              ))}
            </select>
          </div>
          <Button size="sm" onClick={handleRun} isLoading={running}>
            <ShieldCheck className="h-4 w-4 mr-1" />
            Run Check
          </Button>
        </div>
      </div>

      {reports.length === 0 ? (
        <div className="text-center py-8">
          <ShieldCheck className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No continuity reports yet</p>
        </div>
      ) : (
        <div className="space-y-4">
          {reports.map((report) => (
            <div key={report.id} className="border border-gray-200 rounded-lg p-4">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                <span
                  className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                    report.status === 'completed'
                      ? 'bg-green-100 text-green-700'
                      : report.status === 'processing'
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-red-100 text-red-700'
                  }`}
                >
                  {report.status}
                </span>
                <span className="text-sm font-medium text-gray-900">
                  {report.scope === 'chapter'
                    ? `Chapter: ${chapterTitle(report.chapter_id)}`
                    : 'Whole book'}
                </span>
                {report.created_at && (
                  <span className="text-xs text-gray-400">
                    {formatRelativeTime(new Date(report.created_at))}
                  </span>
                )}
                {typeof report.tokens_used === 'number' && (
                  <span className="text-xs text-gray-400">
                    {report.tokens_used.toLocaleString()} tokens
                  </span>
                )}
              </div>

              {report.status === 'processing' ? (
                <Loading size="sm" text="Checking prose..." />
              ) : report.status === 'failed' ? (
                <p className="text-sm text-red-600">This check failed — try again.</p>
              ) : !report.findings || report.findings.length === 0 ? (
                <p className="text-sm text-green-700">
                  No contradictions found. Clean pass.
                </p>
              ) : (
                <div className="space-y-2">
                  {sortedFindings(report.findings).map((finding, i) => (
                    <FindingRow key={i} finding={finding} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
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

  const [activeTab, setActiveTab] = useState<TabKey>('chapters')
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [editOpen, setEditOpen] = useState(false)
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
          if (scene.status === 'processing') {
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
        } catch {
          // transient error — keep polling
          scenePollTimers.current[sceneId] = setTimeout(tick, 5000)
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

          {/* Export */}
          <div className="flex items-center gap-2">
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
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex space-x-4">
          {TABS.map((tab) => {
            const Icon = tab.icon
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`inline-flex items-center px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
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
