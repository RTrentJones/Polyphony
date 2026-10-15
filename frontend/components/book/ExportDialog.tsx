'use client'

/**
 * Export dialog (B9): the book as a manuscript a beta reader can open.
 *
 * Replaces the three bare MD / DOCX / EPUB buttons in the book header with one
 * "Export" that asks the questions an author actually has: which format, the
 * synopsis in front or not (default off — it is the storyboard, not prose),
 * leave out chapters with nothing written (default on), and which chapters
 * (1-based, inclusive, by book position; blank = all of them).
 *
 * The request is GET /books/{id}/export?format=&include_synopsis=&skip_empty=
 * &from_chapter=&to_chapter= (see `exportQuery` in lib/books-store). The range
 * is checked here so an impossible range never reaches the server, which
 * answers 422 for one anyway.
 */

import { useEffect, useId, useState } from 'react'
import { Download } from 'lucide-react'

import Button from '@/components/Button'
import Modal from '@/components/Modal'
import { booksApi } from '@/lib/books-store'
import type { BookExportFormat, BookExportOptions } from '@/lib/types'

export const EXPORT_FORMATS: Array<{ value: BookExportFormat; label: string; hint: string }> = [
  { value: 'md', label: 'Markdown (.md)', hint: 'Plain text with headings' },
  { value: 'docx', label: 'Word (.docx)', hint: 'For editors and beta readers' },
  { value: 'epub', label: 'EPUB (.epub)', hint: 'For e-readers' },
]

/** Parse a chapter-number field: blank → null, otherwise a whole number or NaN. */
function parseChapter(value: string): number | null {
  const v = value.trim()
  if (v === '') return null
  return /^\d+$/.test(v) ? Number(v) : Number.NaN
}

/**
 * Check the chapter range the author typed against the book's chapter count.
 * Returns the parsed bounds, or the message to show next to the fields.
 */
export function validateChapterRange(
  from: string,
  to: string,
  chapterCount: number
): { fromChapter: number | null; toChapter: number | null } | { error: string } {
  const fromChapter = parseChapter(from)
  const toChapter = parseChapter(to)
  const max = Math.max(chapterCount, 1)
  for (const n of [fromChapter, toChapter]) {
    if (n === null) continue
    if (Number.isNaN(n) || n < 1 || n > max) {
      return { error: `Chapter numbers run from 1 to ${max}.` }
    }
  }
  if (fromChapter !== null && toChapter !== null && fromChapter > toChapter) {
    return { error: '"From" must not be after "To".' }
  }
  return { fromChapter, toChapter }
}

export default function ExportDialog({
  isOpen,
  onClose,
  bookId,
  bookTitle,
  chapterCount,
  onExported,
}: {
  isOpen: boolean
  onClose: () => void
  bookId: string
  bookTitle: string
  chapterCount: number
  /** Told once the download has been handed to the browser. */
  onExported?: (format: BookExportFormat) => void
}) {
  const ids = useId()
  const [format, setFormat] = useState<BookExportFormat>('md')
  const [includeSynopsis, setIncludeSynopsis] = useState(false)
  const [skipEmpty, setSkipEmpty] = useState(true)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A failed attempt's message does not outlive the dialog.
  useEffect(() => {
    if (!isOpen) setError(null)
  }, [isOpen])

  const range = validateChapterRange(from, to, chapterCount)
  const rangeError = 'error' in range ? range.error : null

  const handleExport = async () => {
    if ('error' in range || busy) return
    const options: Omit<BookExportOptions, 'format'> = {
      includeSynopsis,
      skipEmpty,
      fromChapter: range.fromChapter,
      toChapter: range.toChapter,
    }
    setBusy(true)
    setError(null)
    try {
      await booksApi.exportBook(bookId, format, bookTitle, options)
      onExported?.(format)
      onClose()
    } catch (err: any) {
      setError(err?.message || `Failed to export ${format.toUpperCase()}`)
    } finally {
      setBusy(false)
    }
  }

  const fieldClass =
    'w-full px-3 py-2 border rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent'

  return (
    <Modal isOpen={isOpen} onClose={busy ? () => {} : onClose} title="Export manuscript" size="md">
      <div className="space-y-5" data-testid="export-dialog">
        <fieldset>
          <legend className="block text-sm font-medium text-gray-700 mb-2">Format</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {EXPORT_FORMATS.map((f) => (
              <label
                key={f.value}
                className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-sm ${
                  format === f.value
                    ? 'border-primary-600 bg-primary-50'
                    : 'border-gray-200 hover:border-primary-200'
                }`}
              >
                <input
                  type="radio"
                  name={`${ids}-format`}
                  value={f.value}
                  checked={format === f.value}
                  onChange={() => setFormat(f.value)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="block font-medium text-gray-900">{f.label}</span>
                  <span className="block text-xs text-gray-500">{f.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={includeSynopsis}
              onChange={(e) => setIncludeSynopsis(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Include synopsis
              <span className="block text-xs text-gray-500">
                Put the book&apos;s synopsis before chapter one.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={skipEmpty}
              onChange={(e) => setSkipEmpty(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Skip empty chapters
              <span className="block text-xs text-gray-500">
                Leave out chapters with no written scene yet.
              </span>
            </span>
          </label>
        </div>

        <fieldset>
          <legend className="block text-sm font-medium text-gray-700 mb-2">Chapters</legend>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor={`${ids}-from`} className="block text-xs text-gray-600 mb-1">
                From chapter
              </label>
              <input
                id={`${ids}-from`}
                type="number"
                inputMode="numeric"
                min={1}
                max={Math.max(chapterCount, 1)}
                placeholder="1"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                aria-invalid={rangeError ? true : undefined}
                aria-describedby={`${ids}-range-help`}
                className={fieldClass + (rangeError ? ' border-red-500' : ' border-gray-300')}
              />
            </div>
            <div>
              <label htmlFor={`${ids}-to`} className="block text-xs text-gray-600 mb-1">
                To chapter
              </label>
              <input
                id={`${ids}-to`}
                type="number"
                inputMode="numeric"
                min={1}
                max={Math.max(chapterCount, 1)}
                placeholder={String(Math.max(chapterCount, 1))}
                value={to}
                onChange={(e) => setTo(e.target.value)}
                aria-invalid={rangeError ? true : undefined}
                aria-describedby={`${ids}-range-help`}
                className={fieldClass + (rangeError ? ' border-red-500' : ' border-gray-300')}
              />
            </div>
          </div>
          <p
            id={`${ids}-range-help`}
            role={rangeError ? 'alert' : undefined}
            className={`mt-1 text-xs ${rangeError ? 'text-red-600' : 'text-gray-500'}`}
          >
            {rangeError ??
              `Leave blank to export every chapter (1–${Math.max(chapterCount, 1)}). Headings keep each chapter's number in the book.`}
          </p>
        </fieldset>

        {error && (
          <div role="alert" className="bg-red-50 border border-red-200 rounded-lg p-3">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}

        <div className="flex items-center gap-3 pt-1">
          <Button variant="outline" fullWidth onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            fullWidth
            onClick={handleExport}
            isLoading={busy}
            disabled={rangeError !== null}
          >
            <Download className="h-4 w-4 mr-1" />
            Download
          </Button>
        </div>
      </div>
    </Modal>
  )
}
