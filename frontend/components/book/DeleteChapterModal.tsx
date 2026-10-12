'use client'

/**
 * Delete a chapter without silently orphaning its prose (SPEC-04).
 *
 * The first click asks the API to delete the chapter as-is. An empty chapter
 * goes; one with scenes answers 409 with the scene count, and this modal then
 * asks the author what to do with them: move them to the end of another chapter
 * of the book, or delete them (their prose is kept in the version log).
 */

import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import Button from '@/components/Button'
import Modal from '@/components/Modal'
import { useBookStore, type ChapterScenesOnDelete } from '@/lib/books-store'
import type { ApiError, BookChapter } from '@/lib/types'

export interface DeleteChapterModalProps {
  bookId: string
  chapter: BookChapter
  /** Every chapter of the book (the deleted one is filtered out). */
  chapters: BookChapter[]
  onClose: () => void
  onDeleted: (message: string) => void
}

export default function DeleteChapterModal({
  bookId,
  chapter,
  chapters,
  onClose,
  onDeleted,
}: DeleteChapterModalProps) {
  const { deleteChapter } = useBookStore()
  const targets = chapters.filter((c) => c.id !== chapter.id)

  // null until the API says the chapter has scenes.
  const [sceneCount, setSceneCount] = useState<number | null>(null)
  const [mode, setMode] = useState<'move' | 'delete'>(targets.length ? 'move' : 'delete')
  const [to, setTo] = useState(targets[0]?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const run = async (scenes?: ChapterScenesOnDelete) => {
    setError(null)
    setWorking(true)
    try {
      await deleteChapter(bookId, chapter.id, scenes)
      onDeleted(
        scenes?.mode === 'move'
          ? `Chapter deleted; its scenes moved to "${targets.find((c) => c.id === to)?.title}"`
          : 'Chapter deleted'
      )
    } catch (err) {
      const e = err as ApiError
      const count = (e.detail as { scene_count?: number } | undefined)?.scene_count
      if (e.status === 409 && typeof count === 'number') {
        setSceneCount(count)
      } else {
        setError(e.message || 'Failed to delete chapter')
      }
    } finally {
      setWorking(false)
    }
  }

  const confirmChoice = () =>
    run(mode === 'move' ? { mode: 'move', to } : { mode: 'delete' })

  return (
    <Modal isOpen onClose={onClose} title={`Delete "${chapter.title}"?`}>
      <div className="space-y-4">
        {sceneCount === null ? (
          <p className="text-sm text-gray-700">This removes the chapter from the book.</p>
        ) : (
          <>
            <div className="flex items-start space-x-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
              <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>
                This chapter has {sceneCount} scene{sceneCount === 1 ? '' : 's'}. What should
                happen to {sceneCount === 1 ? 'it' : 'them'}?
              </span>
            </div>
            <fieldset className="space-y-3 text-sm">
              <label className="flex items-start space-x-2">
                <input
                  type="radio"
                  name="scene-disposition"
                  checked={mode === 'move'}
                  disabled={!targets.length}
                  onChange={() => setMode('move')}
                  className="mt-1"
                />
                <span className="flex-1">
                  Move to the end of
                  {targets.length ? (
                    <select
                      value={to}
                      onChange={(e) => {
                        setTo(e.target.value)
                        setMode('move')
                      }}
                      className="ml-2 rounded border border-gray-300 px-2 py-1"
                      aria-label="Chapter to move the scenes to"
                    >
                      {targets.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.position + 1}. {c.title}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="ml-1 text-gray-500">
                      another chapter (this book has no other chapter)
                    </span>
                  )}
                </span>
              </label>
              <label className="flex items-start space-x-2">
                <input
                  type="radio"
                  name="scene-disposition"
                  checked={mode === 'delete'}
                  onChange={() => setMode('delete')}
                  className="mt-1"
                />
                <span>Delete the scenes with the chapter</span>
              </label>
            </fieldset>
          </>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex items-center space-x-3 pt-2">
          <Button variant="outline" fullWidth onClick={onClose} disabled={working}>
            Cancel
          </Button>
          <Button
            variant="danger"
            fullWidth
            isLoading={working}
            disabled={sceneCount !== null && mode === 'move' && !to}
            onClick={() => (sceneCount === null ? run() : confirmChoice())}
          >
            {sceneCount === null
              ? 'Delete chapter'
              : mode === 'move'
                ? 'Move scenes and delete'
                : 'Delete chapter and scenes'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
