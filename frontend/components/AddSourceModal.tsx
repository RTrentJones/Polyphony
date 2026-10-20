/**
 * AddSourceModal — add source material to a SPECIFIC book (upload a file or paste
 * text). A source is always book-scoped (docs/ADR-002-book-as-root.md §2), so this
 * takes a required bookId and there is no orphan-book path. On success it hands
 * off to the reviewed-extraction flow (nothing is written until approval, R4.4).
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Button from './Button'
import Input from './Input'
import Modal from './Modal'
import FileUpload from './FileUpload'
import ExtractionRefusal from './ExtractionRefusal'
import apiClient from '@/lib/api-client'
import { useExtractionStore, useSourceStore } from '@/lib/store'
import type { ExtractionPreflight } from '@/lib/types'
import { formatFileSize } from '@/lib/utils'

type AddMode = 'upload' | 'paste'

/**
 * What the server can parse (B14, SPEC-21). The dialog asks
 * GET /sources/config; these are the fallback when that fails, and must equal
 * the server's settings.ALLOWED_EXTENSIONS / MAX_UPLOAD_SIZE
 * (tests/unit/test_upload_types.py reads this file to check).
 */
export const ACCEPTED_EXTENSIONS = ['.txt', '.docx', '.pdf', '.html', '.htm']
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

/** Same wording as the server's 415 for a .doc, shown before uploading. */
export const DOC_UNSUPPORTED_MESSAGE =
  "Old Word .doc files aren't supported. Save as .docx and upload again."

type UploadConfig = { accepted_extensions: string[]; max_bytes: number }

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

export function formatsLabel(extensions: string[]): string {
  return extensions.map((e) => e.replace(/^\./, '').toUpperCase()).join(', ')
}

function useUploadConfig(): UploadConfig {
  const [config, setConfig] = useState<UploadConfig>({
    accepted_extensions: ACCEPTED_EXTENSIONS,
    max_bytes: MAX_UPLOAD_BYTES,
  })
  useEffect(() => {
    let alive = true
    apiClient
      .get<UploadConfig>('/sources/config')
      .then(({ data }) => {
        if (alive && Array.isArray(data?.accepted_extensions) && data.accepted_extensions.length) {
          setConfig({
            accepted_extensions: data.accepted_extensions,
            max_bytes: data.max_bytes || MAX_UPLOAD_BYTES,
          })
        }
      })
      .catch(() => {
        // Keep the fallback: it mirrors the server list.
      })
    return () => {
      alive = false
    }
  }, [])
  return config
}

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
  const uploadConfig = useUploadConfig()
  // A picked file the server is known to refuse, with the fix (B14).
  const [fileHint, setFileHint] = useState<string | null>(null)

  const inspectPicked = (picked: File | undefined | null) => {
    if (!picked) return
    const ext = extensionOf(picked.name)
    if (ext === '.doc') {
      setFile(null)
      setFileHint(DOC_UNSUPPORTED_MESSAGE)
    } else {
      setFileHint(null)
    }
  }

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
    return (
      <Modal isOpen onClose={onClose} title="Source saved" size="md">
        <div className="space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}
          <ExtractionRefusal
            preflight={refused.preflight}
            saved
            busy={submitting}
            onExtractOpening={extractOpening}
            onDismiss={onClose}
          />
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
          // Capture-phase listeners see the picked/dropped file before
          // FileUpload validates it, so a .doc gets the "save as .docx" fix
          // instead of the generic "not accepted" line.
          <div
            onChangeCapture={(e) =>
              inspectPicked((e.target as HTMLInputElement).files?.[0])
            }
            onDropCapture={(e) => inspectPicked(e.dataTransfer?.files?.[0])}
          >
            <FileUpload
              label="Source File"
              accept={uploadConfig.accepted_extensions.join(',')}
              maxSize={uploadConfig.max_bytes}
              onFileSelect={(f) => {
                setFileHint(null)
                setFile(f)
              }}
              error={fileHint ?? undefined}
              helperText={`Supported formats: ${formatsLabel(
                uploadConfig.accepted_extensions
              )} (max ${formatFileSize(uploadConfig.max_bytes)})`}
            />
          </div>
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
