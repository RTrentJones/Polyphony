/**
 * Extraction Review — the reviewed-commit step (docs/BRD.md R4.4).
 *
 * Upload PROPOSES canon; nothing is written until the author approves here. This
 * page polls the extraction run, lets the author edit/approve each proposed
 * character, canon entry, style, and synopsis, and commits only the selected
 * items. Voice indexing then runs in the background for the committed cast.
 */

'use client'

import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, Check } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Loading from '@/components/Loading'
import ProtectedRoute from '@/components/ProtectedRoute'
import { useExtractionStore } from '@/lib/store'
import type {
  CanonCategory,
  CanonEntryProposal,
  CharacterProposal,
  ExtractionCommit,
  ExtractionSkipped,
} from '@/lib/types'

const CATEGORIES: CanonCategory[] = [
  'world',
  'location',
  'faction',
  'item',
  'concept',
  'org',
]

type CharRow = CharacterProposal & { approved: boolean }
type EntryRow = CanonEntryProposal & { approved: boolean }

function ReviewContent() {
  const router = useRouter()
  const params = useSearchParams()
  const bookId = params.get('book') as string
  const runId = params.get('run') as string
  const { fetchExtraction, commitExtraction } = useExtractionStore()

  const [status, setStatus] = useState<string>('pending')
  const [error, setError] = useState<string | null>(null)
  const [committing, setCommitting] = useState(false)
  const [skipped, setSkipped] = useState<ExtractionSkipped[]>([])

  const [chars, setChars] = useState<CharRow[]>([])
  const [entries, setEntries] = useState<EntryRow[]>([])
  const [style, setStyle] = useState({
    approved: false,
    pov: '',
    tense: '',
    tone: '',
    comps: '',
  })
  const [synopsis, setSynopsis] = useState({ approved: false, value: '' })

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const run = await fetchExtraction(bookId, runId)
      setStatus(run.status)
      if (run.status === 'ready' && run.proposals && 'characters' in run.proposals) {
        const p = run.proposals
        setChars(p.characters.map((c) => ({ ...c, approved: true })))
        setEntries(p.canon_entries.map((e) => ({ ...e, approved: true })))
        setStyle({
          approved: Boolean(p.style?.pov || p.style?.tense || p.style?.tone),
          pov: p.style?.pov ?? '',
          tense: p.style?.tense ?? '',
          tone: p.style?.tone ?? '',
          comps: p.style?.comps ?? '',
        })
        setSynopsis({ approved: Boolean(p.synopsis), value: p.synopsis ?? '' })
      } else if (run.status === 'pending') {
        timer.current = setTimeout(load, 2000) // still extracting; poll
      } else if (run.status === 'committed') {
        // Already committed (resumed a done run, or a lost-response retry) — the
        // commit is exactly-once, so send the author to the book, not a dead form.
        router.replace(`/books/detail?id=${bookId}`)
      } else if (run.status === 'failed') {
        setError(run.error || 'Extraction failed.')
      }
    } catch (err: any) {
      setError(err.message || 'Could not load the extraction.')
    }
  }, [bookId, runId, fetchExtraction, router])

  useEffect(() => {
    load()
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [load])

  const commit = async () => {
    setCommitting(true)
    setError(null)
    setSkipped([])
    // Send every APPROVED item exactly as shown — never silently filter a
    // blank name (the server skips + surfaces it), and send fields even when
    // empty so clearing a role/description/style/synopsis actually applies
    // (PR review #1, round 6). What you approve is written as shown.
    const payload: ExtractionCommit = {
      characters: chars
        .filter((c) => c.approved)
        .map(({ name, role, description }) => ({ name, role, description })),
      canon_entries: entries
        .filter((e) => e.approved)
        .map(({ name, category, content }) => ({ name, category, content })),
      ...(style.approved
        ? { style: { pov: style.pov, tense: style.tense, tone: style.tone, comps: style.comps } }
        : {}),
      ...(synopsis.approved ? { synopsis: synopsis.value } : {}),
    }
    try {
      const res = await commitExtraction(bookId, runId, payload)
      if (res.result.skipped.length > 0) {
        // Some approved items weren't applied — surface them instead of
        // redirecting as if everything succeeded (PR review #1).
        setSkipped(res.result.skipped)
        setCommitting(false)
        return
      }
      router.push(`/books/detail?id=${bookId}`)
    } catch (err: any) {
      // Exactly-once commit: a run that is no longer `ready` (double-submit, or a
      // retry after a lost response) 409s with a "not 'ready'" detail. That means
      // the work is already DONE, so proceed to the book rather than showing a
      // scary error. A DIFFERENT 409 (a canon name conflict) is a real failure and
      // falls through to the error path (PR review #2, round 6).
      if (err?.status === 409 && /not 'ready'/.test(String(err?.message ?? ''))) {
        router.push(`/books/detail?id=${bookId}`)
        return
      }
      setError(err.message || 'Commit failed.')
      setCommitting(false)
    }
  }

  if (status === 'pending') {
    return (
      <div className="max-w-3xl mx-auto p-6 text-center">
        <Loading />
        <p className="mt-4 text-gray-600">Reading your source and proposing canon…</p>
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <p className="text-red-600 mb-4">{error}</p>
        <Button onClick={() => router.push('/sources')}>Back to Sources</Button>
      </div>
    )
  }

  const setChar = (i: number, patch: Partial<CharRow>) =>
    setChars((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)))
  const setEntry = (i: number, patch: Partial<EntryRow>) =>
    setEntries((es) => es.map((e, idx) => (idx === i ? { ...e, ...patch } : e)))

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-6">
      <button
        onClick={() => router.push('/sources')}
        className="flex items-center text-gray-600 hover:text-gray-900"
      >
        <ArrowLeft className="w-4 h-4 mr-1" /> Sources
      </button>
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Review extracted canon</h1>
        <p className="text-gray-600">
          Nothing is saved until you commit. Approved items are written exactly as
          shown — clearing a field clears it. Uncheck anything you don&apos;t want.
        </p>
      </div>
      {error && <p className="text-red-600">{error}</p>}

      {skipped.length > 0 && (
        <div className="border border-amber-300 bg-amber-50 rounded p-4 space-y-2">
          <p className="font-semibold text-amber-800">
            Committed, but {skipped.length} item
            {skipped.length > 1 ? 's were' : ' was'} not applied:
          </p>
          <ul className="list-disc list-inside text-sm text-amber-800">
            {skipped.map((s, i) => (
              <li key={i}>
                {s.type} “{s.name || '(unnamed)'}” — {s.reason}
              </li>
            ))}
          </ul>
          <Button onClick={() => router.push(`/books/detail?id=${bookId}`)}>
            Continue to book
          </Button>
        </div>
      )}

      <Card>
        <h2 className="font-semibold mb-3">Characters ({chars.length})</h2>
        <div className="space-y-3">
          {chars.map((c, i) => (
            <div key={i} className="flex gap-3 items-start border-b pb-3">
              <input
                type="checkbox"
                checked={c.approved}
                onChange={(e) => setChar(i, { approved: e.target.checked })}
                className="mt-2"
              />
              <div className="flex-1 grid grid-cols-1 sm:grid-cols-3 gap-2">
                <input
                  className="border rounded px-2 py-1"
                  value={c.name}
                  onChange={(e) => setChar(i, { name: e.target.value })}
                  placeholder="Name"
                />
                <input
                  className="border rounded px-2 py-1"
                  value={c.role ?? ''}
                  onChange={(e) => setChar(i, { role: e.target.value })}
                  placeholder="Role"
                />
                <input
                  className="border rounded px-2 py-1"
                  value={c.description ?? ''}
                  onChange={(e) => setChar(i, { description: e.target.value })}
                  placeholder="Description"
                />
              </div>
            </div>
          ))}
          {chars.length === 0 && <p className="text-gray-500">None proposed.</p>}
        </div>
      </Card>

      <Card>
        <h2 className="font-semibold mb-3">Canon entries ({entries.length})</h2>
        <div className="space-y-3">
          {entries.map((e, i) => (
            <div key={i} className="flex gap-3 items-start border-b pb-3">
              <input
                type="checkbox"
                checked={e.approved}
                onChange={(ev) => setEntry(i, { approved: ev.target.checked })}
                className="mt-2"
              />
              <div className="flex-1 space-y-2">
                <div className="flex gap-2">
                  <input
                    className="border rounded px-2 py-1 flex-1"
                    value={e.name}
                    onChange={(ev) => setEntry(i, { name: ev.target.value })}
                    placeholder="Name"
                  />
                  <select
                    className="border rounded px-2 py-1"
                    value={e.category}
                    onChange={(ev) =>
                      setEntry(i, { category: ev.target.value as CanonCategory })
                    }
                  >
                    {CATEGORIES.map((cat) => (
                      <option key={cat} value={cat}>
                        {cat}
                      </option>
                    ))}
                  </select>
                </div>
                <textarea
                  className="border rounded px-2 py-1 w-full"
                  rows={2}
                  value={e.content ?? ''}
                  onChange={(ev) => setEntry(i, { content: ev.target.value })}
                  placeholder="Content"
                />
              </div>
            </div>
          ))}
          {entries.length === 0 && <p className="text-gray-500">None proposed.</p>}
        </div>
      </Card>

      <Card>
        <label className="flex items-center gap-2 font-semibold mb-3">
          <input
            type="checkbox"
            checked={style.approved}
            onChange={(e) => setStyle({ ...style, approved: e.target.checked })}
          />
          Style
        </label>
        <div className="grid grid-cols-2 gap-2">
          {(['pov', 'tense', 'tone', 'comps'] as const).map((f) => (
            <input
              key={f}
              className="border rounded px-2 py-1"
              value={style[f]}
              onChange={(e) => setStyle({ ...style, [f]: e.target.value })}
              placeholder={f}
            />
          ))}
        </div>
      </Card>

      <Card>
        <label className="flex items-center gap-2 font-semibold mb-3">
          <input
            type="checkbox"
            checked={synopsis.approved}
            onChange={(e) => setSynopsis({ ...synopsis, approved: e.target.checked })}
          />
          Synopsis
        </label>
        <textarea
          className="border rounded px-2 py-1 w-full"
          rows={4}
          value={synopsis.value}
          onChange={(e) => setSynopsis({ ...synopsis, value: e.target.value })}
        />
      </Card>

      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={() => router.push('/sources')}>
          Cancel
        </Button>
        <Button onClick={commit} disabled={committing}>
          <Check className="w-4 h-4 mr-1" />
          {committing ? 'Committing…' : 'Commit approved'}
        </Button>
      </div>
    </div>
  )
}

export default function ExtractionReviewPage() {
  return (
    <ProtectedRoute>
      <Suspense fallback={<Loading />}>
        <ReviewContent />
      </Suspense>
    </ProtectedRoute>
  )
}
