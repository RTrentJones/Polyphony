/**
 * Scenes panel — every scene the book owns (chapter + standalone), readable.
 */

'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Film, Trash2, Wand2 } from 'lucide-react'
import Button from '@/components/Button'
import GenerateSceneModal, {
  GENERATE_BLOCKER_ID,
  useGenerateBlocker,
} from '@/components/book/GenerateSceneModal'
import Card from '@/components/Card'
import Loading from '@/components/Loading'
import SceneGrounding from '@/components/SceneGrounding'
import type { ToastType } from '@/components/Toast'
import { booksApi } from '@/lib/books-store'
import { useBook } from '@/components/book/BookProvider'
import type { BookSceneSummary } from '@/lib/types'
import { SCENE_STATUS_STYLES } from '@/components/book/styles'
import JobStatusBadge from '@/components/JobStatusBadge'
import { isActiveJob } from '@/lib/jobs'
import { rateLimitedPollDelay } from '@/lib/rate-limit'

export default function ScenesPanel({
  addToast,
}: {
  addToast: (message: string, type: ToastType) => void
}) {
  const router = useRouter()
  const { bookId, chapters } = useBook()
  const [scenes, setScenes] = useState<BookSceneSummary[]>([])
  const [loading, setLoading] = useState(true)
  // Live job status per scene, from its badge: the list is fetched once, but a
  // pause should read as a pause as soon as it happens.
  const [liveStatus, setLiveStatus] = useState<Record<string, string>>({})
  const [genOpen, setGenOpen] = useState(false)
  const blocker = useGenerateBlocker()
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const s = await booksApi.listBookScenes(bookId)
      setScenes(s)
      if (pollRef.current) clearTimeout(pollRef.current)
      // Generating scenes land in the background; paused ones resume when the
      // AI quota returns. A scene with a live job is watched by its badge
      // (useJob), which reloads on settle; poll only for any without one.
      if (
        s.some(
          (x) =>
            (x.status === 'processing' || x.status === 'paused') &&
            !isActiveJob(x.job?.status)
        )
      ) {
        pollRef.current = setTimeout(load, 5000)
      }
    } catch (err: any) {
      // Rate-limited (SPEC-05): wait the long way round instead of toasting.
      const backoff = rateLimitedPollDelay(err)
      if (backoff !== null) {
        if (pollRef.current) clearTimeout(pollRef.current)
        pollRef.current = setTimeout(load, backoff)
        return
      }
      addToast(err.message || 'Failed to load scenes', 'error')
    } finally {
      setLoading(false)
    }
  }, [bookId, addToast])

  useEffect(() => {
    load()
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [load])

  const del = async (id: string, title: string | null) => {
    if (!confirm(`Move scene "${title || 'Untitled'}" to the Trash?`)) return
    try {
      await booksApi.deleteScene(id)
      addToast('Scene moved to the Trash', 'success')
      load()
    } catch (err: any) {
      addToast(err.message || 'Delete failed', 'error')
    }
  }

  if (loading) return <Loading text="Loading scenes…" />

  return (
    <Card>
      <div className="flex items-start justify-between mb-4 gap-4">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Scenes</h2>
          <p className="text-sm text-gray-500">
            Every scene in this book — each drafted into a chapter.
          </p>
          {blocker && (
            <p id={GENERATE_BLOCKER_ID} className="text-sm text-gray-600">
              {blocker}
            </p>
          )}
        </div>
        <Button
          size="sm"
          onClick={() => setGenOpen(true)}
          disabled={!!blocker}
          aria-describedby={blocker ? GENERATE_BLOCKER_ID : undefined}
        >
          <Wand2 className="h-4 w-4 mr-1" />
          Generate scene
        </Button>
      </div>
      {scenes.length === 0 ? (
        <div className="text-center py-8">
          <Film className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No scenes yet — open a chapter and generate one.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {scenes.map((s) => (
            <div
              key={s.id}
              className="flex items-start justify-between gap-3 p-3 rounded-lg border border-gray-200"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-medium text-gray-900 truncate">
                    {s.title || 'Untitled scene'}
                  </h3>
                  {isActiveJob(s.job?.status) ? (
                    <JobStatusBadge
                      jobId={s.job?.job_id}
                      initial={s.job}
                      onSettled={() => load()}
                      onStatus={(st) =>
                        setLiveStatus((m) => ({ ...m, [s.id]: st }))
                      }
                    />
                  ) : (
                    <span
                      className={`px-2 py-0.5 text-xs rounded ${
                        SCENE_STATUS_STYLES[s.status] || 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {s.status}
                    </span>
                  )}
                  <span className="text-xs text-gray-500">
                    {s.chapter_title || 'Unfiled'}
                  </span>
                </div>
                {s.characters && s.characters.length > 0 && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {s.characters.join(', ')}
                  </p>
                )}
                <p className="text-sm text-gray-600 line-clamp-2 mt-1">
                  {s.status === 'paused' || liveStatus[s.id] === 'paused'
                    ? 'Paused — daily AI quota reached; resumes automatically.'
                    : s.preview ||
                      (s.status === 'processing'
                        ? 'Generating…'
                        : 'No content yet')}
                </p>
                {s.status === 'completed' && <SceneGrounding sceneId={s.id} />}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    router.push(`/books/editor?scene=${s.id}&book=${bookId}`)
                  }
                >
                  Read
                </Button>
                <Button variant="ghost" size="sm" onClick={() => del(s.id, s.title)}>
                  <Trash2 className="h-4 w-4 text-red-500" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {genOpen && (
        <GenerateSceneModal
          bookId={bookId}
          chapters={chapters}
          onClose={() => setGenOpen(false)}
          onNotice={(m) => addToast(m, 'info')}
          onGenerated={() => {
            setGenOpen(false)
            load() // the new 'processing' scene appears + polls to completion
          }}
        />
      )}
    </Card>
  )
}
