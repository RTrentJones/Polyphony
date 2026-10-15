'use client'

/**
 * "Generate all planned scenes" for a promoted chapter (SPEC-10 follow-up).
 *
 * One scene per planned beat, with the chapter's promoted cast and POV. The
 * server writes them one after another, so each scene starts from the one
 * before it, and preflights the whole batch: a batch the budget cannot cover
 * is refused with the estimate rather than stopping halfway. Beats that
 * already have a scene are skipped, so clicking again after a failure picks
 * up where it stopped. Shown only when the chapter has planned beats (`chapter.planned_beats`,
 * or the legacy summary lines; see plannedBeats).
 */

import { useState } from 'react'
import { Layers } from 'lucide-react'

import Button from '@/components/Button'
import { plannedBeats } from '@/components/book/GenerateSceneModal'
import { useBookStore } from '@/lib/books-store'
import type { BookChapter, PlannedScenesResponse } from '@/lib/types'

export function plannedScenesMessage(r: PlannedScenesResponse): string {
  if (r.already_running) {
    return 'The planned scenes for this chapter are already being written.'
  }
  if (r.queued === 0) {
    return 'Every planned beat in this chapter already has a scene.'
  }
  const skipped = r.skipped
    ? ` (${r.skipped} already written, skipped)`
    : ''
  return `Writing ${r.queued} planned scene${r.queued === 1 ? '' : 's'} in order${skipped}.`
}

export default function GeneratePlannedScenesButton({
  chapter,
  onDone,
}: {
  chapter: BookChapter
  /** Told the outcome (or the error) as a message, and its kind. */
  onDone: (message: string, kind: 'success' | 'info' | 'error') => void
}) {
  const { generatePlannedScenes } = useBookStore()
  const [busy, setBusy] = useState(false)
  const beats = plannedBeats(chapter)
  if (beats.length === 0) return null

  const run = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await generatePlannedScenes(chapter.id)
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
      title={`One scene per planned beat (${beats.length}), written in order`}
    >
      <Layers className="h-4 w-4 mr-1" />
      Generate all planned scenes
    </Button>
  )
}
