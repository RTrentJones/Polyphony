/**
 * VoicePanel — one character's voice, visible and editable (SPEC-11, R5.2–R5.3).
 *
 * The voice samples retrieved from the vector store ARE the grounding behind
 * every generated line, so the author can browse, fix, delete and add them,
 * see what a query would retrieve (with scores and the threshold generation
 * uses), and try a line of dialogue against them.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import Button from './Button'
import { type ToastType } from './Toast'
import {
  splitSamples,
  voiceApi,
  type DialogueTestResult,
  type RetrievalResult,
} from '@/lib/voice-api'
import type { VoiceChunk } from '@/lib/types'

export const VOICE_PAGE_SIZE = 20
const CHUNK_TYPES = ['dialogue', 'action', 'thought', 'description']

const textareaClass =
  'w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500'

export function formatScore(score: number | null | undefined): string {
  return typeof score === 'number' ? score.toFixed(3) : '—'
}

export default function VoicePanel({
  characterId,
  characterName,
  addToast,
}: {
  characterId: string
  characterName: string
  addToast: (message: string, type: ToastType) => void
}) {
  const [chunks, setChunks] = useState<VoiceChunk[]>([])
  const [total, setTotal] = useState(0)
  const [skip, setSkip] = useState(0)
  const [typeFilter, setTypeFilter] = useState('')
  const [loading, setLoading] = useState(true)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const [samplesText, setSamplesText] = useState('')
  const [sampleType, setSampleType] = useState('dialogue')
  const [adding, setAdding] = useState(false)

  const [query, setQuery] = useState('')
  const [retrieval, setRetrieval] = useState<RetrievalResult | null>(null)
  const [retrieving, setRetrieving] = useState(false)

  const [linePrompt, setLinePrompt] = useState('')
  const [line, setLine] = useState<DialogueTestResult | null>(null)
  const [testing, setTesting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await voiceApi.listChunks(characterId, {
        skip,
        limit: VOICE_PAGE_SIZE,
        ...(typeFilter ? { chunk_type: typeFilter } : {}),
      })
      setChunks(page.chunks)
      setTotal(page.total)
    } catch (err: any) {
      addToast(err.message || 'Failed to load voice samples', 'error')
    } finally {
      setLoading(false)
    }
  }, [characterId, skip, typeFilter, addToast])

  useEffect(() => {
    load()
  }, [load])

  const saveEdit = async (chunk: VoiceChunk) => {
    const text = draft.trim()
    if (!text) return
    try {
      await voiceApi.updateChunk(characterId, chunk.id, text)
      setEditingId(null)
      addToast('Sample saved and re-embedded', 'success')
      load()
    } catch (err: any) {
      addToast(err.message || 'Failed to save sample', 'error')
    }
  }

  const remove = async (chunk: VoiceChunk) => {
    if (!confirm('Delete this voice sample? This cannot be undone.')) return
    try {
      await voiceApi.deleteChunk(characterId, chunk.id)
      addToast('Sample deleted', 'success')
      // Stepping back keeps the author on a non-empty page after deleting the last row.
      if (chunks.length === 1 && skip > 0) setSkip(Math.max(0, skip - VOICE_PAGE_SIZE))
      else load()
    } catch (err: any) {
      addToast(err.message || 'Delete failed', 'error')
    }
  }

  const addSamples = async () => {
    const samples = splitSamples(samplesText)
    if (samples.length === 0) return
    setAdding(true)
    try {
      const { indexed } = await voiceApi.addSamples(characterId, samples, sampleType)
      setSamplesText('')
      addToast(`Added ${indexed} sample${indexed === 1 ? '' : 's'}`, 'success')
      load()
    } catch (err: any) {
      addToast(err.message || 'Failed to add samples', 'error')
    } finally {
      setAdding(false)
    }
  }

  const runRetrieval = async () => {
    if (!query.trim()) return
    setRetrieving(true)
    try {
      setRetrieval(await voiceApi.retrieve(characterId, query.trim()))
    } catch (err: any) {
      addToast(err.message || 'Retrieval failed', 'error')
    } finally {
      setRetrieving(false)
    }
  }

  const runLine = async () => {
    if (linePrompt.trim().length < 3) return
    setTesting(true)
    try {
      setLine(await voiceApi.testLine(characterId, linePrompt.trim()))
    } catch (err: any) {
      addToast(err.message || 'Test failed', 'error')
    } finally {
      setTesting(false)
    }
  }

  const last = Math.min(skip + chunks.length, total)

  return (
    <div className="space-y-5 pt-3" data-testid="voice-panel">
      {/* Samples */}
      <section>
        <div className="flex items-center justify-between gap-2 mb-2">
          <h3 className="text-sm font-semibold text-gray-900">
            Voice samples ({total})
          </h3>
          <select
            aria-label="Filter by type"
            value={typeFilter}
            onChange={(e) => {
              setSkip(0)
              setTypeFilter(e.target.value)
            }}
            className="px-2 py-1 border border-gray-300 rounded text-xs"
          >
            <option value="">All types</option>
            {CHUNK_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        {loading ? (
          <p className="text-sm text-gray-500">Loading samples…</p>
        ) : chunks.length === 0 ? (
          <p className="text-sm text-gray-500">
            No voice samples yet — add some below so {characterName} has a voice to match.
          </p>
        ) : (
          <ul className="space-y-2">
            {chunks.map((c) => (
              <li
                key={c.id}
                data-testid="voice-chunk"
                className="p-2 rounded border border-gray-200 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs text-gray-500">
                    <span className="px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded">
                      {c.chunk_type}
                    </span>
                    <span className="ml-2">{c.source || 'manual'}</span>
                    <span className="ml-2">{c.word_count} words</span>
                  </p>
                  {editingId !== c.id && (
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label="Edit sample"
                        onClick={() => {
                          setEditingId(c.id)
                          setDraft(c.text)
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label="Delete sample"
                        onClick={() => remove(c)}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-red-500" />
                      </Button>
                    </div>
                  )}
                </div>
                {editingId === c.id ? (
                  <div className="space-y-2 mt-1">
                    <textarea
                      aria-label="Sample text"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      rows={3}
                      className={textareaClass}
                    />
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => saveEdit(c)} disabled={!draft.trim()}>
                        Save
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setEditingId(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="text-gray-800 whitespace-pre-wrap mt-1">{c.text}</p>
                )}
              </li>
            ))}
          </ul>
        )}
        {total > VOICE_PAGE_SIZE && (
          <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
            <span>
              {skip + 1}–{last} of {total}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={skip === 0}
                onClick={() => setSkip(Math.max(0, skip - VOICE_PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={last >= total}
                onClick={() => setSkip(skip + VOICE_PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </section>

      {/* Add samples */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-900">Add samples</h3>
        <textarea
          aria-label="New samples"
          value={samplesText}
          onChange={(e) => setSamplesText(e.target.value)}
          rows={4}
          placeholder="Paste lines in this character's voice. One sample per paragraph — separate them with a blank line."
          className={textareaClass}
        />
        <div className="flex items-center gap-2">
          <select
            aria-label="Sample type"
            value={sampleType}
            onChange={(e) => setSampleType(e.target.value)}
            className="px-2 py-1 border border-gray-300 rounded text-xs"
          >
            {CHUNK_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            isLoading={adding}
            disabled={splitSamples(samplesText).length === 0}
            onClick={addSamples}
          >
            Add {splitSamples(samplesText).length || ''} sample
            {splitSamples(samplesText).length === 1 ? '' : 's'}
          </Button>
        </div>
      </section>

      {/* Retrieval inspector */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-900">Test retrieval</h3>
        <div className="flex gap-2">
          <input
            aria-label="Retrieval query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runRetrieval()}
            placeholder="A moment to ground, e.g. “she refuses the offer”"
            className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
          />
          <Button size="sm" isLoading={retrieving} disabled={!query.trim()} onClick={runRetrieval}>
            Retrieve
          </Button>
        </div>
        {retrieval && <RetrievalList result={retrieval} />}
      </section>

      {/* Test a line */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-900">Test a line</h3>
        <div className="flex gap-2">
          <input
            aria-label="Line prompt"
            value={linePrompt}
            onChange={(e) => setLinePrompt(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runLine()}
            placeholder={`What is ${characterName} responding to?`}
            className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm"
          />
          <Button
            size="sm"
            isLoading={testing}
            disabled={linePrompt.trim().length < 3}
            onClick={runLine}
          >
            Generate line
          </Button>
        </div>
        {line && (
          <div className="p-2 rounded border border-gray-200 text-sm space-y-1" data-testid="test-line">
            <p className="text-gray-900">“{line.dialogue}”</p>
            {line.action && <p className="text-gray-600 italic">{line.action}</p>}
            {typeof line.confidence_score === 'number' && (
              <p className="text-xs text-gray-500">
                Voice consistency {formatScore(line.confidence_score)}
              </p>
            )}
            {line.grounding && line.grounding.length > 0 && (
              <div>
                <p className="text-xs text-gray-500">Grounded on:</p>
                <ul className="text-xs text-gray-600 list-disc ml-4">
                  {line.grounding.map((g, i) => (
                    <li key={g.chunk_id || i}>
                      <span className="font-mono">{formatScore(g.score)}</span> {g.text}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}

/** Ranked hits with the threshold line drawn where generation stops using them. */
function RetrievalList({ result }: { result: RetrievalResult }) {
  if (result.results.length === 0) {
    return <p className="text-sm text-gray-500">No samples to retrieve.</p>
  }
  const above = (s: number | null) => s === null || s >= result.threshold
  const firstBelow = result.results.findIndex((h) => !above(h.score))
  const line = (
    <li
      key="threshold"
      data-testid="threshold-line"
      className="flex items-center gap-2 text-xs text-amber-700"
    >
      <span className="flex-1 border-t border-dashed border-amber-400" />
      threshold {result.threshold.toFixed(2)}
      {firstBelow === 0 &&
        ' — nothing clears it, so generation falls back to the closest samples'}
      <span className="flex-1 border-t border-dashed border-amber-400" />
    </li>
  )
  const rows = result.results.map((h, i) => (
    <li
      key={h.id || i}
      data-testid="retrieval-hit"
      className={`flex gap-2 text-sm ${above(h.score) ? '' : 'opacity-60'}`}
    >
      <span className="font-mono text-xs text-gray-700 w-12 shrink-0">
        {formatScore(h.score)}
      </span>
      <span className="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded h-fit">
        {h.chunk_type}
      </span>
      <span className="text-gray-800">{h.text}</span>
    </li>
  ))
  rows.splice(firstBelow >= 0 ? firstBelow : rows.length, 0, line)
  return <ol className="space-y-1">{rows}</ol>
}
