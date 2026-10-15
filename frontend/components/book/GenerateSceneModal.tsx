'use client'

/**
 * Generate a scene into a chapter.
 *
 * The cast is picked from THIS book's characters (SPEC-01). It used to be a
 * free-text box plus a picker over every source the user had, across all
 * books, and a name that matched nothing produced voice-blind prose. The
 * backend now rejects any character that is not in the book.
 */

import { useEffect, useMemo, useState } from 'react'
import { Users, Wand2 } from 'lucide-react'

import Button from '@/components/Button'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import EstimateHint from '@/components/EstimateHint'
import Modal from '@/components/Modal'
import { canonApi } from '@/lib/canon-store'
import { useBookStore } from '@/lib/books-store'
import type {
  BookChapter,
  Character,
  ChapterSceneRequest,
  PlannedBeat,
} from '@/lib/types'
import type { Estimate } from '@/lib/usage-api'

/** The scene description a planned beat generates with: "title: summary",
 *  byte-identical to the server's beat_scene_description (planned_beat.py). */
export function beatDescription(beat: Pick<PlannedBeat, 'title' | 'summary'>): string {
  return `${beat.title ?? ''}: ${beat.summary ?? ''}`.trim()
}

/** '' is "Book's tone": the backend leaves the tone to the canon's style. */
const TONES = [
  '',
  'tense',
  'joyful',
  'melancholic',
  'suspenseful',
  'romantic',
  'wry',
]

export interface GenerateSceneModalProps {
  bookId: string
  /** A fixed target chapter (from the Chapters tab). */
  chapter?: BookChapter
  /** Or a list to pick from (from the Scenes tab). */
  chapters?: BookChapter[]
  /** Pre-filled scene description (e.g. a planned beat). */
  initialDescription?: string
  onClose: () => void
  onGenerated: (sceneId: string, chapterId: string) => void
  /** Told when the scene runs differently from what was asked (e.g. an
   *  ensemble request the tier runs as prose), so a downgrade is never silent. */
  onNotice?: (message: string) => void
}

export default function GenerateSceneModal({
  bookId,
  chapter,
  chapters,
  initialDescription,
  onClose,
  onGenerated,
  onNotice,
}: GenerateSceneModalProps) {
  const { generateScene } = useBookStore()

  const [chapterId, setChapterId] = useState(
    chapter?.id ?? chapters?.[0]?.id ?? ''
  )
  const [cast, setCast] = useState<Character[]>([])
  const [loadingCast, setLoadingCast] = useState(true)
  const [selected, setSelected] = useState<string[]>([])
  const [description, setDescription] = useState(initialDescription ?? '')
  const [setting, setSetting] = useState('')
  const [tone, setTone] = useState<string>('')
  const [beatId, setBeatId] = useState<string | null>(null)
  // True while a picked beat's cast is left to the server (no toggle since).
  const [castFromBeat, setCastFromBeat] = useState(false)
  const [povId, setPovId] = useState('')
  const [wordCount, setWordCount] = useState(800)
  const [styleNotes, setStyleNotes] = useState('')
  const [mode, setMode] = useState<'prose' | 'ensemble'>('prose')
  const [est, setEst] = useState<Estimate | null>(null)
  // One key per modal: every submit from this form is the same request.
  const [idempotencyKey] = useState(() =>
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    canonApi
      .listCharacters(bookId)
      .then((chars) => {
        if (!cancelled) setCast(chars)
      })
      .catch(() => {
        if (!cancelled) setCast([])
      })
      .finally(() => {
        if (!cancelled) setLoadingCast(false)
      })
    return () => {
      cancelled = true
    }
  }, [bookId])

  const byId = useMemo(() => new Map(cast.map((c) => [c.id, c])), [cast])

  const activeChapter =
    chapter ?? chapters?.find((c) => c.id === chapterId) ?? undefined
  // The server's planned beats are the only source (SPEC-23).
  const beats = activeChapter?.planned_beats ?? []

  // A chapter promoted from the outline carries its cast and POV (SPEC-10):
  // pre-select them when the cast loads or the target chapter changes. Only
  // characters still in the book's cast are selected.
  useEffect(() => {
    if (loadingCast || !activeChapter) return
    const ids = (activeChapter.character_ids ?? []).filter((id) => byId.has(id))
    if (ids.length === 0) return
    setSelected(ids)
    const pov = activeChapter.pov_character_id
    setPovId(pov && ids.includes(pov) ? pov : '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingCast, activeChapter?.id])

  // A planned beat fills the description, setting and tone, and links the
  // scene to the beat by id (SPEC-23), so generate-all-planned never writes it
  // twice. Its cast is the server's (review W-1), exactly as the batch casts
  // it: the beat's own cast and anyone it names who is now in the book, else
  // the chapter's, plus the chapter POV. The picker previews the beat's own
  // ids; once the author toggles someone, their pick is sent instead.
  const pickBeat = (beat: PlannedBeat) => {
    setDescription(beatDescription(beat))
    setSetting(beat.setting ?? '')
    setTone(beat.tone ?? '')
    const own = beat.character_ids.filter((id) => byId.has(id))
    if (own.length > 0) setSelected(povId && !own.includes(povId) ? [povId, ...own] : own)
    setCastFromBeat(true)
    setBeatId(beat.node_id)
  }

  const toggle = (id: string) => {
    setCastFromBeat(false)
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    )
  }

  const handleSubmit = async () => {
    setError(null)
    const serverCast = Boolean(beatId) && castFromBeat
    if (selected.length === 0 && !serverCast) {
      setError('Pick at least one character from the cast')
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
    const pov = povId ? byId.get(povId)?.name : undefined
    const request: ChapterSceneRequest = {
      character_ids: serverCast ? [] : selected,
      scene_description: description.trim(),
      setting: setting.trim(),
      emotional_tone: tone,
      ...(beatId ? { planned_beat_id: beatId } : {}),
      ...(pov ? { pov_character: pov } : {}),
      target_word_count: Math.min(3000, Math.max(100, wordCount || 800)),
      ...(styleNotes.trim() ? { style_notes: styleNotes.trim() } : {}),
      mode,
    }
    setSubmitting(true)
    try {
      const result = await generateScene(chapterId, {
        ...request,
        idempotencyKey,
      } as ChapterSceneRequest)
      if (result.mode_reason) onNotice?.(result.mode_reason)
      onGenerated(result.scene_id, chapterId)
      onClose()
    } catch (err: any) {
      setError(err.message || 'Failed to start scene generation')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={chapter ? `Generate scene — ${chapter.title}` : 'Generate scene'}
      size="lg"
    >
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}

        {!chapter && chapters && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Chapter
            </label>
            <select
              value={chapterId}
              onChange={(e) => {
                setChapterId(e.target.value)
                setBeatId(null)
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            <Users className="inline h-4 w-4 mr-1" />
            Who is in this scene?
          </label>
          {loadingCast ? (
            <Loading size="sm" text="Loading the cast..." />
          ) : cast.length === 0 ? (
            <p className="text-sm text-gray-500" data-testid="empty-cast">
              This book has no characters yet. Add them in the Canon tab (or
              extract them from a source) before generating a scene.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2" data-testid="cast-picker">
              {cast.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => toggle(c.id)}
                  aria-pressed={selected.includes(c.id)}
                  className={`px-3 py-1 text-sm rounded-full border transition-colors ${
                    selected.includes(c.id)
                      ? 'border-primary-500 bg-primary-50 text-primary-700'
                      : 'border-gray-300 text-gray-700 hover:border-primary-300'
                  }`}
                >
                  {c.name}
                  {c.role ? (
                    <span className="ml-1 text-xs text-gray-400">· {c.role}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Scene description
          </label>
          {beats.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2" data-testid="planned-beats">
              <span className="text-xs text-gray-500 self-center">
                Planned beats:
              </span>
              {beats.map((beat) => {
                const text = beatDescription(beat)
                const extra = [beat.setting, beat.tone].filter(Boolean).join(' · ')
                return (
                  <button
                    key={beat.node_id}
                    type="button"
                    onClick={() => pickBeat(beat)}
                    aria-pressed={beatId === beat.node_id}
                    title={extra ? `${text}\n${extra}` : text}
                    className="px-2 py-0.5 text-xs rounded border border-gray-300 text-gray-700 hover:border-primary-300 text-left"
                  >
                    {text.length > 60 ? `${text.slice(0, 57)}…` : text}
                    {extra && (
                      <span className="block text-[11px] text-gray-400" data-testid="beat-meta">
                        {extra}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
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
              {(TONES.includes(tone) ? TONES : [...TONES, tone]).map((t) => (
                <option key={t} value={t}>
                  {t ? t.charAt(0).toUpperCase() + t.slice(1) : "Book's tone"}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Point of view
            </label>
            <select
              value={povId}
              onChange={(e) => setPovId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">As the style guide says</option>
              {selected.map((id) => (
                <option key={id} value={id}>
                  {byId.get(id)?.name}
                </option>
              ))}
            </select>
          </div>
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
            Style notes for this scene (optional — override the style guide)
          </label>
          <textarea
            value={styleNotes}
            onChange={(e) => setStyleNotes(e.target.value)}
            placeholder="e.g., Terse sentences, no adverbs"
            rows={2}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            How to write it
          </label>
          <div className="flex gap-4 text-sm" role="radiogroup">
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="mode"
                checked={mode === 'prose'}
                onChange={() => setMode('prose')}
              />
              Prose — one pass per beat
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="mode"
                checked={mode === 'ensemble'}
                onChange={() => setMode('ensemble')}
              />
              Ensemble — each character proposes, an editor reviews
            </label>
          </div>
        </div>

        <EstimateHint
          request={
            selected.length > 0 && bookId
              ? {
                  action: 'scene',
                  book_id: bookId,
                  character_ids: selected,
                  target_word_count: Math.min(3000, Math.max(100, wordCount || 800)),
                  mode,
                }
              : null
          }
          onEstimate={setEst}
        />

        <div className="flex items-center space-x-3 pt-4">
          <Button variant="outline" fullWidth onClick={onClose}>
            Cancel
          </Button>
          <Button
            fullWidth
            onClick={handleSubmit}
            isLoading={submitting}
            disabled={
              (est !== null && !est.allowed) ||
              selected.length === 0 ||
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
