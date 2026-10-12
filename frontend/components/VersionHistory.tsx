/**
 * VersionHistory — list → preview → restore for ANY versioned book entity
 * (synopsis, character, canon_entry, style_guide, book_plan, source, chapter).
 * Restore is forward-only: it appends a new head, so the list refreshes after
 * (docs/ADR-002 §5). Drives the "everything restorable if there's a version" CX
 * uniformly. Restoring a source's text OFFERS re-extraction (SPEC-13); it spends
 * quota and proposes canon, so it never runs on its own.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import Button from './Button'
import Loading from './Loading'
import Modal from './Modal'
import apiClient, { toApiError } from '@/lib/api-client'
import { canonApi } from '@/lib/canon-store'
import type { EntityVersionSummary, VersionedEntityType } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'

export default function VersionHistory({
  bookId,
  entityType,
  entityId,
  label,
  onClose,
  onRestored,
}: {
  bookId: string
  entityType: VersionedEntityType
  entityId: string
  label: string
  onClose: () => void
  /** Called after a successful restore so the parent can refetch the live value. */
  onRestored: () => void
}) {
  const [versions, setVersions] = useState<EntityVersionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ no: number; content: string } | null>(null)
  const [restoringNo, setRestoringNo] = useState<number | null>(null)
  const [reextract, setReextract] = useState<'offered' | 'running' | 'started' | null>(
    null
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setVersions(await canonApi.listVersions(bookId, entityType, entityId))
    } catch (err: any) {
      setError(err.message || 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }, [bookId, entityType, entityId])

  useEffect(() => {
    load()
  }, [load])

  const showPreview = async (no: number) => {
    setError(null)
    try {
      const v = await canonApi.getVersion(bookId, entityType, entityId, no)
      setPreview({ no, content: JSON.stringify(v.content, null, 2) })
    } catch (err: any) {
      setError(err.message || 'Failed to load version')
    }
  }

  const restore = async (no: number) => {
    setRestoringNo(no)
    setError(null)
    try {
      const result = await canonApi.restoreVersion(bookId, entityType, entityId, no)
      setReextract(result?.reextract?.offered ? 'offered' : null)
      onRestored()
      setPreview(null)
      await load() // restore appended a new head
    } catch (err: any) {
      setError(err.message || 'Restore failed')
    } finally {
      setRestoringNo(null)
    }
  }

  const runReextract = async () => {
    setReextract('running')
    try {
      await apiClient.post(`/sources/${entityId}/process`)
      setReextract('started')
    } catch (err) {
      setError(toApiError(err).message || 'Could not start extraction')
      setReextract('offered')
    }
  }

  return (
    <Modal isOpen onClose={onClose} title={`History — ${label}`} size="lg">
      {error && <p className="text-red-600 text-sm mb-3">{error}</p>}
      {reextract && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-primary-50 p-3 text-sm text-primary-900">
          {reextract === 'started' ? (
            <span>Extraction started. Review its proposals from the Sources tab.</span>
          ) : (
            <>
              <span>The source text changed. Re-run extraction to review canon from it?</span>
              <Button size="sm" isLoading={reextract === 'running'} onClick={runReextract}>
                Re-run extraction
              </Button>
            </>
          )}
        </div>
      )}
      {loading ? (
        <Loading text="Loading history…" />
      ) : versions.length === 0 ? (
        <p className="text-gray-600 text-sm">No versions yet.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2 max-h-96 overflow-auto">
            {versions.map((v, i) => (
              <div
                key={v.version_no}
                className="flex items-center justify-between gap-2 p-2 rounded border border-gray-200"
              >
                <button
                  onClick={() => showPreview(v.version_no)}
                  className="text-left min-w-0 flex-1 hover:text-primary-700"
                >
                  <p className="text-sm font-medium text-gray-900">
                    v{v.version_no}
                    {i === 0 ? ' · current' : ''}
                    {v.reason ? ` · ${v.reason}` : ''}
                  </p>
                  <p className="text-xs text-gray-500">
                    {v.created_at ? formatRelativeTime(new Date(v.created_at)) : ''}
                  </p>
                </button>
                {i !== 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    isLoading={restoringNo === v.version_no}
                    onClick={() => restore(v.version_no)}
                  >
                    <RotateCcw className="h-3.5 w-3.5 mr-1" />
                    Restore
                  </Button>
                )}
              </div>
            ))}
          </div>
          <div className="md:border-l md:border-gray-100 md:pl-4">
            {preview ? (
              <>
                <p className="text-xs text-gray-500 mb-1">v{preview.no} snapshot</p>
                <pre className="text-xs bg-gray-50 rounded p-2 overflow-auto max-h-80 whitespace-pre-wrap">
                  {preview.content}
                </pre>
              </>
            ) : (
              <p className="text-sm text-gray-400">Select a version to preview.</p>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}
