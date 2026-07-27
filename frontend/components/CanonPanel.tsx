/**
 * CanonPanel — the book's Canon in one place: synopsis, characters, canon
 * entries, and style. Everything is editable, and everything versioned is
 * restorable (History → VersionHistory). Realizes the book-rooted CX: the
 * aggregate is the book, so all its canon lives and is managed here.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { History, Pencil, Plus, Trash2 } from 'lucide-react'
import Button from './Button'
import Card from './Card'
import Input from './Input'
import Loading from './Loading'
import Modal from './Modal'
import VersionHistory from './VersionHistory'
import { type ToastType } from './Toast'
import { useBookStore } from '@/lib/books-store'
import { canonApi } from '@/lib/canon-store'
import type {
  CanonCategory,
  CanonEntry,
  Character,
  CharacterDetail,
  StyleGuide,
  VersionedEntityType,
} from '@/lib/types'

const CATEGORIES: CanonCategory[] = [
  'world',
  'location',
  'faction',
  'item',
  'concept',
  'org',
]

type HistoryTarget = { type: VersionedEntityType; id: string; label: string }

export default function CanonPanel({
  bookId,
  synopsis,
  onBookChanged,
  addToast,
}: {
  bookId: string
  synopsis: string | null | undefined
  /** Refetch the book so the header synopsis reflects an edit/restore. */
  onBookChanged: () => void
  addToast: (message: string, type: ToastType) => void
}) {
  const { updateBook } = useBookStore()
  const [entries, setEntries] = useState<CanonEntry[]>([])
  const [style, setStyle] = useState<StyleGuide | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [loading, setLoading] = useState(true)

  const [history, setHistory] = useState<HistoryTarget | null>(null)
  const [editingSynopsis, setEditingSynopsis] = useState(false)
  const [synopsisDraft, setSynopsisDraft] = useState('')
  const [charEdit, setCharEdit] = useState<CharacterDetail | null>(null)
  const [charAdd, setCharAdd] = useState(false)
  const [entryEdit, setEntryEdit] = useState<Partial<CanonEntry> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [canon, chars] = await Promise.all([
        canonApi.getCanon(bookId),
        canonApi.listCharacters(bookId),
      ])
      setEntries(canon.entries)
      setStyle(canon.style)
      setCharacters(chars)
    } catch (err: any) {
      addToast(err.message || 'Failed to load canon', 'error')
    } finally {
      setLoading(false)
    }
  }, [bookId, addToast])

  useEffect(() => {
    load()
  }, [load])

  const reloadAll = () => {
    load()
    onBookChanged()
  }

  // -- Synopsis ---------------------------------------------------------------
  const saveSynopsis = async () => {
    try {
      await updateBook(bookId, { synopsis: synopsisDraft })
      setEditingSynopsis(false)
      onBookChanged()
      addToast('Synopsis saved', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to save synopsis', 'error')
    }
  }

  if (loading) return <Loading text="Loading canon…" />

  return (
    <div className="space-y-6">
      {/* Synopsis */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">Synopsis</h2>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setHistory({ type: 'synopsis', id: bookId, label: 'Synopsis' })
              }
            >
              <History className="h-4 w-4 mr-1" />
              History
            </Button>
            {!editingSynopsis && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setSynopsisDraft(synopsis || '')
                  setEditingSynopsis(true)
                }}
              >
                <Pencil className="h-3.5 w-3.5 mr-1" />
                Edit
              </Button>
            )}
          </div>
        </div>
        {editingSynopsis ? (
          <div className="space-y-3">
            <textarea
              value={synopsisDraft}
              onChange={(e) => setSynopsisDraft(e.target.value)}
              rows={6}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={saveSynopsis}>
                Save
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditingSynopsis(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-gray-700 whitespace-pre-wrap">
            {synopsis || (
              <span className="italic text-gray-400">No synopsis yet.</span>
            )}
          </p>
        )}
      </Card>

      {/* Characters */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">
            Characters ({characters.length})
          </h2>
          <Button size="sm" onClick={() => setCharAdd(true)}>
            <Plus className="h-4 w-4 mr-1" />
            Add
          </Button>
        </div>
        {characters.length === 0 ? (
          <p className="text-sm text-gray-500">No characters yet.</p>
        ) : (
          <div className="space-y-2">
            {characters.map((c) => (
              <div
                key={c.id}
                className="flex items-start justify-between gap-3 p-3 rounded-lg border border-gray-200"
              >
                <div className="min-w-0">
                  <p className="font-medium text-gray-900">
                    {c.name}
                    {c.role ? (
                      <span className="ml-2 text-xs text-gray-500">{c.role}</span>
                    ) : null}
                  </p>
                  {c.description && (
                    <p className="text-sm text-gray-600 line-clamp-2">
                      {c.description}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setHistory({
                        type: 'character',
                        id: c.id,
                        label: c.name,
                      })
                    }
                  >
                    <History className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      try {
                        setCharEdit(await canonApi.getCharacter(c.id))
                      } catch (err: any) {
                        addToast(err.message || 'Failed to load character', 'error')
                      }
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      if (!confirm(`Delete "${c.name}"? You can restore it.`)) return
                      try {
                        await canonApi.deleteCharacter(c.id)
                        addToast('Character deleted', 'success')
                        load()
                      } catch (err: any) {
                        addToast(err.message || 'Delete failed', 'error')
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Canon entries */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">
            Canon entries ({entries.length})
          </h2>
          <Button
            size="sm"
            onClick={() => setEntryEdit({ name: '', category: 'concept', content: '' })}
          >
            <Plus className="h-4 w-4 mr-1" />
            Add
          </Button>
        </div>
        {entries.length === 0 ? (
          <p className="text-sm text-gray-500">No canon entries yet.</p>
        ) : (
          <div className="space-y-2">
            {entries.map((e) => (
              <div
                key={e.id}
                className="flex items-start justify-between gap-3 p-3 rounded-lg border border-gray-200"
              >
                <div className="min-w-0">
                  <p className="font-medium text-gray-900">
                    {e.name}
                    <span className="ml-2 text-xs px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded">
                      {e.category}
                    </span>
                  </p>
                  {e.content && (
                    <p className="text-sm text-gray-600 line-clamp-2">{e.content}</p>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setHistory({ type: 'canon_entry', id: e.id, label: e.name })
                    }
                  >
                    <History className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setEntryEdit(e)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      if (!confirm(`Delete "${e.name}"? You can restore it.`)) return
                      try {
                        await canonApi.deleteEntry(bookId, e.id)
                        addToast('Entry deleted', 'success')
                        load()
                      } catch (err: any) {
                        addToast(err.message || 'Delete failed', 'error')
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Style */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">Style</h2>
          {style?.id && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setHistory({ type: 'style_guide', id: style.id, label: 'Style' })
              }
            >
              <History className="h-4 w-4 mr-1" />
              History
            </Button>
          )}
        </div>
        <StyleEditor
          style={style}
          onSave={async (patch) => {
            try {
              const saved = await canonApi.upsertStyle(bookId, patch)
              setStyle(saved)
              addToast('Style saved', 'success')
            } catch (err: any) {
              addToast(err.message || 'Failed to save style', 'error')
            }
          }}
        />
      </Card>

      {history && (
        <VersionHistory
          bookId={bookId}
          entityType={history.type}
          entityId={history.id}
          label={history.label}
          onClose={() => setHistory(null)}
          onRestored={reloadAll}
        />
      )}

      {(charEdit || charAdd) && (
        <CharacterModal
          character={charEdit}
          onClose={() => {
            setCharEdit(null)
            setCharAdd(false)
          }}
          onSave={async (fields) => {
            try {
              if (charEdit) {
                await canonApi.updateCharacter(charEdit.id, fields)
              } else {
                await canonApi.createCharacter(bookId, {
                  name: fields.name || 'Unnamed',
                  role: fields.role || undefined,
                  description: fields.description || undefined,
                })
              }
              setCharEdit(null)
              setCharAdd(false)
              addToast('Character saved', 'success')
              load()
            } catch (err: any) {
              addToast(err.message || 'Failed to save character', 'error')
            }
          }}
        />
      )}

      {entryEdit && (
        <EntryModal
          entry={entryEdit}
          onClose={() => setEntryEdit(null)}
          onSave={async (fields) => {
            try {
              if (entryEdit.id) {
                await canonApi.updateEntry(bookId, entryEdit.id, fields)
              } else {
                await canonApi.createEntry(bookId, {
                  name: fields.name || '',
                  category: fields.category || 'concept',
                  content: fields.content || '',
                })
              }
              setEntryEdit(null)
              addToast('Entry saved', 'success')
              load()
            } catch (err: any) {
              addToast(err.message || 'Failed to save entry', 'error')
            }
          }}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

function StyleEditor({
  style,
  onSave,
}: {
  style: StyleGuide | null
  onSave: (patch: Partial<StyleGuide>) => Promise<void>
}) {
  const [pov, setPov] = useState(style?.pov || '')
  const [tense, setTense] = useState(style?.tense || '')
  const [tone, setTone] = useState(style?.tone || '')
  const [comps, setComps] = useState(style?.comps || '')
  const [sample, setSample] = useState(style?.sample_prose || '')
  const [saving, setSaving] = useState(false)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Input label="POV" value={pov} onChange={(e) => setPov(e.target.value)} />
        <Input
          label="Tense"
          value={tense}
          onChange={(e) => setTense(e.target.value)}
        />
        <Input label="Tone" value={tone} onChange={(e) => setTone(e.target.value)} />
        <Input
          label="Comps"
          value={comps}
          onChange={(e) => setComps(e.target.value)}
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Sample prose
        </label>
        <textarea
          value={sample}
          onChange={(e) => setSample(e.target.value)}
          rows={4}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
        />
      </div>
      <Button
        size="sm"
        isLoading={saving}
        onClick={async () => {
          setSaving(true)
          await onSave({
            pov,
            tense,
            tone,
            comps,
            sample_prose: sample,
          })
          setSaving(false)
        }}
      >
        Save style
      </Button>
    </div>
  )
}

function CharacterModal({
  character,
  onClose,
  onSave,
}: {
  character: CharacterDetail | null
  onClose: () => void
  onSave: (fields: {
    name: string
    role: string
    description: string
    goals: string
    arc: string
    notes: string
  }) => Promise<void>
}) {
  const [name, setName] = useState(character?.name || '')
  const [role, setRole] = useState(character?.role || '')
  const [description, setDescription] = useState(character?.description || '')
  const [goals, setGoals] = useState(character?.goals || '')
  const [arc, setArc] = useState(character?.arc || '')
  const [notes, setNotes] = useState(character?.notes || '')
  const [saving, setSaving] = useState(false)

  const field = (label: string, value: string, set: (v: string) => void, rows = 2) => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <textarea
        value={value}
        onChange={(e) => set(e.target.value)}
        rows={rows}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
      />
    </div>
  )

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={character ? `Edit — ${character.name}` : 'Add character'}
      size="lg"
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input label="Role" value={role} onChange={(e) => setRole(e.target.value)} />
        </div>
        {field('Description', description, setDescription, 3)}
        {field('Goals', goals, setGoals)}
        {field('Arc', arc, setArc)}
        {field('Notes', notes, setNotes)}
        <div className="flex gap-3 pt-2">
          <Button variant="outline" fullWidth onClick={onClose}>
            Cancel
          </Button>
          <Button
            fullWidth
            isLoading={saving}
            disabled={!name.trim()}
            onClick={async () => {
              setSaving(true)
              await onSave({ name, role, description, goals, arc, notes })
              setSaving(false)
            }}
          >
            Save
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function EntryModal({
  entry,
  onClose,
  onSave,
}: {
  entry: Partial<CanonEntry>
  onClose: () => void
  onSave: (fields: {
    name: string
    category: string
    content: string
  }) => Promise<void>
}) {
  const [name, setName] = useState(entry.name || '')
  const [category, setCategory] = useState<string>(entry.category || 'concept')
  const [content, setContent] = useState(entry.content || '')
  const [saving, setSaving] = useState(false)

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={entry.id ? `Edit — ${entry.name}` : 'Add canon entry'}
      size="md"
    >
      <div className="space-y-3">
        <div className="flex gap-3">
          <div className="flex-1">
            <Input
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Category
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              {CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Content
          </label>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={5}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
        </div>
        <div className="flex gap-3 pt-2">
          <Button variant="outline" fullWidth onClick={onClose}>
            Cancel
          </Button>
          <Button
            fullWidth
            isLoading={saving}
            disabled={!name.trim()}
            onClick={async () => {
              setSaving(true)
              await onSave({ name, category, content })
              setSaving(false)
            }}
          >
            Save
          </Button>
        </div>
      </div>
    </Modal>
  )
}
