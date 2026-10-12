/**
 * AddSourceModal — add source material to a SPECIFIC book (upload a file or paste
 * text). A source is always book-scoped (docs/ADR-002-book-as-root.md §2), so this
 * takes a required bookId and there is no orphan-book path. On success it hands
 * off to the reviewed-extraction flow (nothing is written until approval, R4.4).
 */

'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Button from './Button'
import Input from './Input'
import Modal from './Modal'
import FileUpload from './FileUpload'
import { useExtractionStore, useSourceStore } from '@/lib/store'
import type { ExtractionPreflight } from '@/lib/types'

type AddMode = 'upload' | 'paste'

export default function AddSourceModal({
  bookId,
  onClose,
}: {
  bookId: string
  onClose: () => void
}) {
  const router = useRouter()
  const { uploadSource, pasteSource } = useSourceStore()
  const { startExtraction } = useExtractionStore()
  // Set when the preflight refused a whole-source extraction (SPEC-09): the
  // source is saved; the author may extract its opening chapters instead.
  const [refused, setRefused] = useState<{
    sourceId: string
    bookId: string
    preflight: ExtractionPreflight
  } | null>(null)

  const [mode, setMode] = useState<AddMode>('upload')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (!title.trim()) {
      setError('Please give the source a title')
      return
    }
    if (mode === 'upload' && !file) {
      setError('Please choose a file')
      return
    }
    if (mode === 'paste' && !pasteText.trim()) {
      setError('Please paste some text')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const res =
        mode === 'upload'
          ? await uploadSource(file as File, title.trim(), undefined, bookId)
          : await pasteSource(title.trim(), pasteText, bookId)
      if (!res.extraction_run_id && res.preflight && !res.preflight.fits) {
        setRefused({ sourceId: res.id, bookId: res.book_id, preflight: res.preflight })
        setSubmitting(false)
        return
      }
      onClose()
      // Nothing is written until the author approves (docs/BRD.md R4.4).
      router.push(`/sources/review?book=${res.book_id}&run=${res.extraction_run_id}`)
    } catch (err: any) {
      setError(err.message || 'Could not add the source. Please try again.')
      setSubmitting(false)
    }
  }

  const extractOpening = async () => {
    if (!refused?.preflight.max_chapters) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await startExtraction(
        refused.bookId,
        refused.sourceId,
        refused.preflight.max_chapters
      )
      onClose()
      router.push(`/sources/review?book=${refused.bookId}&run=${res.run_id}`)
    } catch (err: any) {
      setError(err.message || 'Could not start extraction.')
      setSubmitting(false)
    }
  }

  if (refused) {
    const p = refused.preflight
    return (
      <Modal isOpen onClose={onClose} title="Source saved" size="md">
        <div className="space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}
          <p className="text-sm text-gray-700">
            Your source is saved, but extracting all of it would take about{' '}
            {p.estimated_tokens.toLocaleString()} tokens over {p.windows} parts (
            {p.requests} AI requests) — more than the{' '}
            {p.capacity_tokens.toLocaleString()} available before the job would
            give up waiting for quota.
          </p>
          <div className="flex items-center space-x-3 pt-2">
            <Button variant="outline" fullWidth onClick={onClose}>
              Not now
            </Button>
            {p.max_chapters ? (
              <Button fullWidth onClick={extractOpening} isLoading={submitting}>
                Extract the first {p.max_chapters} chapters
              </Button>
            ) : null}
          </div>
        </div>
      </Modal>
    )
  }

  return (
    <Modal isOpen onClose={onClose} title="Add Source" size="md">
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}

        <div className="flex rounded-lg border border-gray-200 p-1 bg-gray-50">
          {(['upload', 'paste'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`flex-1 text-sm py-1.5 rounded-md transition-colors ${
                mode === m
                  ? 'bg-white shadow text-gray-900 font-medium'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {m === 'upload' ? 'Upload file' : 'Paste text'}
            </button>
          ))}
        </div>

        <Input
          label="Source Title"
          placeholder="Enter a title for your source"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />

        {mode === 'upload' ? (
          <FileUpload
            label="Source File"
            accept=".txt,.doc,.docx,.pdf"
            maxSize={10 * 1024 * 1024} // 10MB
            onFileSelect={(f) => setFile(f)}
            helperText="Supported formats: TXT, DOC, DOCX, PDF (max 10MB)"
          />
        ) : (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Text
            </label>
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="Paste your notes, a scene, character descriptions…"
              rows={10}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
            <p className="text-xs text-gray-500 mt-1">
              We&apos;ll extract characters and canon for you to review — nothing is
              saved until you approve it.
            </p>
          </div>
        )}

        <div className="flex items-center space-x-3 pt-4">
          <Button variant="outline" fullWidth onClick={onClose}>
            Cancel
          </Button>
          <Button
            fullWidth
            onClick={submit}
            isLoading={submitting}
            disabled={
              !title.trim() || (mode === 'upload' ? !file : !pasteText.trim())
            }
          >
            {mode === 'upload' ? 'Upload' : 'Add text'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
