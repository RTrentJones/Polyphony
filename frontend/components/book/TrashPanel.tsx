/**
 * Trash panel — what was deleted from this book, and what restoring it brings
 * back (SPEC-13). Deleting is soft everywhere; only "Delete forever" here is
 * permanent, and it asks for the item's name typed out first.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, RotateCcw, Trash2 } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import type { ToastType } from '@/components/Toast'
import { trashApi } from '@/lib/trash-api'
import type { ApiError, TrashConflictDetail, TrashEntityType, TrashItem } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'

const TYPE_LABELS: Record<TrashEntityType, string> = {
  character: 'Character',
  canon_entry: 'Canon entry',
  source: 'Source',
  chapter: 'Chapter',
  scene: 'Scene',
}

export default function TrashPanel({
  bookId,
  addToast,
  onChanged,
}: {
  bookId: string
  addToast: (message: string, type: ToastType) => void
  /** Called after a restore, so the page can refetch what came back. */
  onChanged?: () => void
}) {
  const [items, setItems] = useState<TrashItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [conflict, setConflict] = useState<{ id: string; message: string } | null>(null)
  const [purging, setPurging] = useState<TrashItem | null>(null)
  const [typed, setTyped] = useState('')

  const load = useCallback(async () => {
    try {
      setItems(await trashApi.list(bookId))
      setError(null)
    } catch (err) {
      setError((err as ApiError).message || 'Failed to load the Trash')
    }
  }, [bookId])

  useEffect(() => {
    load()
  }, [load])

  const restore = async (item: TrashItem) => {
    setBusyId(item.id)
    setConflict(null)
    try {
      await trashApi.restore(bookId, item.type, item.id)
      addToast(`Restored "${item.name}"`, 'success')
      onChanged?.()
      await load()
    } catch (err) {
      const e = err as ApiError
      if (e.status === 409) {
        // Nothing changed; say what is in the way, next to the item.
        const d = e.detail as TrashConflictDetail | undefined
        setConflict({ id: item.id, message: d?.message || e.message })
      } else {
        addToast(e.message || 'Restore failed', 'error')
      }
    } finally {
      setBusyId(null)
    }
  }

  const purge = async () => {
    if (!purging || typed !== purging.name) return
    setBusyId(purging.id)
    try {
      await trashApi.purge(bookId, purging.type, purging.id)
      addToast(`"${purging.name}" deleted forever`, 'success')
      setPurging(null)
      await load()
    } catch (err) {
      addToast((err as ApiError).message || 'Delete failed', 'error')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card>
      <div className="mb-4">
        <h2 className="text-xl font-semibold text-gray-900">Trash</h2>
        <p className="text-sm text-gray-500">
          Deleted characters, canon, sources, chapters and scenes wait here until you
          restore them or delete them forever.
        </p>
      </div>

      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
      {items === null && !error ? (
        <Loading text="Loading the Trash…" />
      ) : items && items.length === 0 ? (
        <p className="text-sm text-gray-500 py-6 text-center">The Trash is empty.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {(items ?? []).map((item) => (
            <li key={`${item.type}-${item.id}`} className="py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    <span className="mr-2 rounded bg-gray-100 px-1.5 py-0.5 text-xs font-normal text-gray-600">
                      {TYPE_LABELS[item.type]}
                    </span>
                    {item.name}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Deleted {formatRelativeTime(new Date(item.deleted_at))} · Restores{' '}
                    {item.summary}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button
                    variant="outline"
                    size="sm"
                    isLoading={busyId === item.id && !purging}
                    onClick={() => restore(item)}
                    aria-label={`Restore ${item.name}`}
                  >
                    <RotateCcw className="h-3.5 w-3.5 mr-1" />
                    Restore
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setPurging(item)
                      setTyped('')
                    }}
                    aria-label={`Delete ${item.name} forever`}
                  >
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                </div>
              </div>
              {conflict?.id === item.id && (
                <p role="alert" className="mt-2 text-sm text-amber-800">
                  {conflict.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {purging && (
        <Modal isOpen onClose={() => setPurging(null)} title="Delete forever?">
          <div className="space-y-4">
            <div className="flex items-start space-x-2 rounded-lg bg-red-50 p-3 text-sm text-red-900">
              <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>
                This permanently deletes {TYPE_LABELS[purging.type].toLowerCase()}{' '}
                <strong>{purging.name}</strong> and {purging.summary}. It cannot be
                restored from the Trash afterwards.
              </span>
            </div>
            <label className="block text-sm text-gray-700">
              Type <strong>{purging.name}</strong> to confirm
              <input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
                aria-label="Type the name to confirm"
                autoFocus
              />
            </label>
            <div className="flex items-center space-x-3">
              <Button variant="outline" fullWidth onClick={() => setPurging(null)}>
                Cancel
              </Button>
              <Button
                variant="danger"
                fullWidth
                disabled={typed !== purging.name}
                isLoading={busyId === purging.id}
                onClick={purge}
              >
                Delete forever
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </Card>
  )
}
