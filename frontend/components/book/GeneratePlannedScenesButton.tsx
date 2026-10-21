'use client'

/**
 * "Generate all planned scenes" for a promoted chapter (SPEC-10 follow-up).
 *
 * One scene per planned beat, with the chapter's promoted cast and POV. The
 * server writes them one after another, so each scene starts from the one
 * before it. It queues the scenes the budget covers now and says why it
 * deferred the rest (SPEC-34). Beats that already have a scene are skipped,
 * so clicking again picks up where it stopped. Shown only when the chapter
 * has planned beats (`chapter.planned_beats`, the server's only source; SPEC-23).
 */

import { useState } from 'react'
import { Layers } from 'lucide-react'

import Button from '@/components/Button'
import { GENERATE_BLOCKER_ID } from '@/components/book/GenerateSceneModal'
import { useBookStore } from '@/lib/books-store'
import { modelFor } from '@/lib/usage-api'
import type { BookChapter, PlannedScenesResponse } from '@/lib/types'

export function plannedScenesMessage(r: PlannedScenesResponse): string {
  const deferred = r.deferred > 0 ? ` ${r.reason}` : ''
  if (r.already_running) {
    return `The planned scenes for this chapter are already being written.${deferred}`
  }
  if (r.queued === 0) {
    return 'Every planned beat in this chapter already has a scene.'
  }
  const skipped = r.skipped
    ? ` (${r.skipped} already written, skipped)`
    : ''
  return `Writing ${r.queued} planned scene${r.queued === 1 ? '' : 's'} in order${skipped}.${deferred}`
}

export default function GeneratePlannedScenesButton({
  chapter,
  blocker,
  onDone,
}: {
  chapter: BookChapter
  /** generateBlocker's reason: disables the button and describes it. */
  blocker: string | null
  /** Told the outcome (or the error) as a message, and its kind. */
  onDone: (message: string, kind: 'success' | 'info' | 'error') => void
}) {
  const { generatePlannedScenes } = useBookStore()
  const [busy, setBusy] = useState(false)
  const beats = chapter.planned_beats ?? []
  if (beats.length === 0) return null

  const run = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await generatePlannedScenes(chapter.id, modelFor(chapter.book_id))
      onDone(
        plannedScenesMessage(r),
        r.queued > 0 && !r.already_running ? 'success' : 'info'
      )
    } catch (err: any) {
      onDone(err?.message || 'Failed to start the planned scenes', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={run}
      isLoading={busy}
      disabled={!!blocker}
      aria-describedby={blocker ? GENERATE_BLOCKER_ID : undefined}
      title={`One scene per planned beat (${beats.length}), written in order`}
    >
      <Layers className="h-4 w-4 mr-1" />
      Generate all planned scenes
    </Button>
  )
}
