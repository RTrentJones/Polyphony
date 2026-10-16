'use client'

/**
 * Edit a chapter's title, summary and planned beats before generating
 * (SPEC-35). One form, one Save, one PATCH carrying only what changed: a beat
 * whose chips were never toggled keeps its stored `character_ids` ([] follows
 * the chapter cast), and a rename sends no `planned_beats`.
 */

import { type ChangeEvent, useEffect, useState } from 'react'
import Link from 'next/link'
import { Plus, Trash2 } from 'lucide-react'

import Button from '@/components/Button'
import CastChips, { chapterPovLock } from '@/components/book/CastChips'
import { useBook } from '@/components/book/BookProvider'
import { useBookStore } from '@/lib/books-store'
import type { ApiError, ChapterDetail, PlannedBeat, PlannedBeatInput } from '@/lib/types'

type FormBeat = PlannedBeat & { key: string; touchedCast: boolean }

const input =
  'w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-primary-500'

/** Ids still in the cast; all of them while the cast has not loaded (empty). */
const keepLive = (ids: string[], live: Set<string>) =>
  live.size ? ids.filter((id) => live.has(id)) : ids

/** What Save sends for a beat: the shown chips only if the author toggled them,
 * else its stored ids less any character since moved to the Trash (no chip
 * shows one, and the server refuses it). */
const toInput = (
  { key, touchedCast, cast_ids, ...b }: FormBeat,
  live: Set<string>
): PlannedBeatInput => ({
  ...b,
  character_ids: touchedCast ? cast_ids : keepLive(b.character_ids, live),
})
/** The stored beats as Save would send them unedited, so a rename alone sends no beats. */
const stored = (ch: ChapterDetail, live: Set<string>): PlannedBeatInput[] =>
  (ch.planned_beats ?? []).map(({ cast_ids, ...b }) => ({
    ...b,
    character_ids: keepLive(b.character_ids, live),
  }))

/** "k of n planned scenes written", from the server's `written` (D4). */
export function writtenLabel(ch: ChapterDetail): string {
  const beats = ch.planned_beats ?? []
  const k = beats.filter((b) => ch.written[b.node_id]).length
  return `${k} of ${beats.length} planned scenes written`
}

/** "written" and a link to the beat's scene in the editor, or nothing. */
export function WrittenLink({ chapter, nodeId }: { chapter: ChapterDetail; nodeId: string }) {
  const { bookId } = useBook()
  const sceneId = chapter.written[nodeId]
  if (!sceneId) return null
  const title = chapter.scenes.find((s) => s.id === sceneId)?.title
  return (
    <Link
      href={`/books/editor?scene=${sceneId}&book=${bookId}`}
      className="text-xs text-primary-700 underline"
    >
      written{title ? ` · ${title}` : ''}
    </Link>
  )
}

export default function PlannedBeatsEditor({
  chapter,
  onSaved,
  onCancel,
}: {
  chapter: ChapterDetail
  onSaved: () => void
  onCancel: () => void
}) {
  const { characters, refreshCharacters } = useBook()
  const { updateChapter } = useBookStore()
  const n = chapter.number
  const [title, setTitle] = useState(chapter.title)
  const [summary, setSummary] = useState(chapter.summary ?? '')
  const [beats, setBeats] = useState<FormBeat[]>(() =>
    (chapter.planned_beats ?? []).map((b, i) => ({ ...b, key: `b${i}`, touchedCast: false }))
  )
  const [error, setError] = useState<{ message: string; unknown: string[] } | null>(null)
  const [saving, setSaving] = useState(false)
  const live = new Set(characters.map((c) => c.id))
  const locked = chapterPovLock(chapter, characters.map((c) => c.id))

  useEffect(() => {
    refreshCharacters().catch(() => undefined)
  }, [refreshCharacters])

  const edit = (i: number, patch: Partial<FormBeat>) =>
    setBeats((bs) => bs.map((b, j) => (j === i ? { ...b, ...patch } : b)))
  const toggle = (i: number, id: string) =>
    edit(i, {
      touchedCast: true,
      cast_ids: beats[i].cast_ids.includes(id)
        ? beats[i].cast_ids.filter((x) => x !== id)
        : [...beats[i].cast_ids, id],
    })
  const addBeat = () =>
    setBeats((bs) => [
      ...bs,
      // node_id '': the server mints the id (planned_beat.py).
      { node_id: '', title: '', summary: '', character_ids: [], secondary_names: [], setting: '',
        tone: '', cast_ids: chapter.character_ids ?? [], key: `new${Date.now()}`, touchedCast: false },
    ])

  const handleSave = async () => {
    if (!title.trim()) return setError({ message: 'The chapter needs a title.', unknown: [] })
    if (beats.some((b) => !b.title.trim())) {
      return setError({ message: 'Every beat needs a title. Remove a beat to drop it.', unknown: [] })
    }
    const out = beats.map((b) => toInput(b, live))
    const body = {
      ...(title !== chapter.title ? { title } : {}),
      ...(summary !== (chapter.summary ?? '') ? { summary } : {}),
      ...(JSON.stringify(out) !== JSON.stringify(stored(chapter, live)) ? { planned_beats: out } : {}),
    }
    if (Object.keys(body).length === 0) return onCancel()
    setSaving(true)
    setError(null)
    try {
      await updateChapter(chapter.id, body)
      onSaved()
    } catch (err) {
      const e = err as ApiError
      const d = e.detail as { message?: string; unknown?: string[] } | undefined
      // A strict-cast 422 marks its beats; any other error (a Pydantic 422's
      // first msg, via toApiError) shows above the form. Nothing is saved.
      setError({
        message: (d?.unknown && d.message) || e.message,
        unknown: d?.unknown ?? [],
      })
    } finally {
      setSaving(false)
    }
  }

  const alert = (message: string) => (
    <p role="alert" className="text-sm text-red-700">
      {message}
    </p>
  )
  // The one text control: a one-line input, or a two-row textarea for a summary.
  const text = (label: string, value: string, set: (v: string) => void, summary = false) => {
    const Tag = summary ? 'textarea' : 'input'
    return (
      <Tag
        aria-label={label}
        placeholder={label.split(', ')[1]}
        rows={summary ? 2 : undefined}
        className={input}
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement & HTMLTextAreaElement>) => set(e.target.value)}
      />
    )
  }

  return (
    <div className="space-y-3" data-testid="beats-editor">
      {error && error.unknown.length === 0 && alert(error.message)}
      {text(`Title of chapter ${n}`, title, setTitle)}
      {text(`Summary of chapter ${n}`, summary, setSummary, true)}
      <p className="text-sm text-gray-600">{writtenLabel(chapter)}</p>
      {beats.map((b, i) => {
        const beat = `beat ${i + 1} of chapter ${n}`
        const Beat = `Beat ${i + 1} of chapter ${n}`
        const field = (k: 'title' | 'summary' | 'setting' | 'tone') =>
          text(`${Beat}, ${k}`, b[k], (v) => edit(i, { [k]: v }), k === 'summary')
        return (
          <div
            key={b.key}
            role="group"
            aria-label={Beat}
            className="space-y-1 rounded border border-gray-200 p-2"
          >
            <div className="flex items-center gap-1">
              {field('title')}
              <button
                type="button"
                aria-label={`Delete ${beat}`}
                onClick={() => setBeats((bs) => bs.filter((x) => x !== b))}
                className="p-1 text-gray-500 hover:text-gray-900"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
            {field('summary')}
            <div className="grid grid-cols-2 gap-1">
              {field('setting')}
              {field('tone')}
            </div>
            <CastChips
              cast={characters}
              selected={b.cast_ids}
              locked={locked}
              onToggle={(id) => toggle(i, id)}
              label={`Cast of ${beat}`}
            />
            {b.secondary_names.length > 0 && (
              <p className="text-xs text-gray-600">Also named: {b.secondary_names.join(', ')}</p>
            )}
            {error &&
              toInput(b, live).character_ids.some((id) => error.unknown.includes(id)) &&
              alert(error.message)}
            {chapter.written[b.node_id] && (
              <p className="text-xs text-gray-600">
                <WrittenLink chapter={chapter} nodeId={b.node_id} /> Editing this
                beat does not change its scene. Edit the scene, or delete it and generate again.
              </p>
            )}
          </div>
        )
      })}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={addBeat} aria-label={`Add beat to chapter ${n}`}>
          <Plus className="h-4 w-4 mr-1" />
          Add beat
        </Button>
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} isLoading={saving} aria-label={`Save chapter ${n}`}>
          Save
        </Button>
      </div>
    </div>
  )
}
