/**
 * Getting started — the guided path through an empty book (SPEC-27, B12).
 *
 * A new book opens on eight tabs, each with its own empty state. This
 * checklist names the next step instead: Add source → Review the cast →
 * Generate an outline → Draft a scene. Every tick comes from the book's real
 * data (sources, characters, a ready outline, a completed scene), never from
 * clicks, and each step deep-links to the action. It hides once all four are
 * done, or when the author dismisses it (remembered per book in localStorage).
 *
 * The action buttons' names deliberately avoid the tab names and the page's
 * other button names ("Add source", "Outline", "Generate…"), so a by-name
 * lookup of those still finds exactly one button.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Circle, X } from 'lucide-react'
import Button from '@/components/Button'
import apiClient, { toApiError } from '@/lib/api-client'
import { booksApi } from '@/lib/books-store'
import { canonApi } from '@/lib/canon-store'
import type { BookPlanListResponse, SourceListResponse } from '@/lib/types'

/** What the checklist ticks from. */
export interface GettingStartedProgress {
  sources: number
  characters: number
  /** A ready outline with at least one node. */
  outline: boolean
  /** Completed scenes. */
  scenes: number
}

export type GettingStartedAction = 'add-source' | 'canon' | 'outline' | 'chapters'

interface Step {
  key: GettingStartedAction
  label: string
  hint: string
  action: string
  done: (p: GettingStartedProgress) => boolean
}

export const GETTING_STARTED_STEPS: Step[] = [
  {
    key: 'add-source',
    label: 'Add source',
    hint: 'Upload or paste your manuscript, notes or world bible.',
    action: 'Add your first source',
    done: (p) => p.sources > 0,
  },
  {
    key: 'canon',
    label: 'Review the cast',
    hint: 'Check the characters extracted from your source, or add your own.',
    action: 'Review the cast',
    done: (p) => p.characters > 0,
  },
  {
    key: 'outline',
    label: 'Generate an outline',
    hint: 'Turn the synopsis and canon into chapters and planned scenes.',
    action: 'Start planning',
    done: (p) => p.outline,
  },
  {
    key: 'chapters',
    label: 'Draft a scene',
    hint: 'Generate the first scene of a chapter, then edit it.',
    action: 'Write a scene',
    done: (p) => p.scenes > 0,
  },
]

const dismissKey = (bookId: string) => `polyphony:getting-started:dismissed:${bookId}`

function readDismissed(bookId: string): boolean {
  try {
    return window.localStorage.getItem(dismissKey(bookId)) === '1'
  } catch {
    return false
  }
}

function writeDismissed(bookId: string) {
  try {
    window.localStorage.setItem(dismissKey(bookId), '1')
  } catch {
    // private mode / blocked storage: dismissal lasts for this page only
  }
}

/** The book's progress, from the same endpoints the tabs read. */
export async function fetchGettingStartedProgress(
  bookId: string
): Promise<GettingStartedProgress> {
  const [sources, characters, plans, scenes] = await Promise.all([
    apiClient
      .get<SourceListResponse>('/sources/', { params: { book_id: bookId } })
      .then(({ data }) => data.sources.filter((s) => s.book_id === bookId).length)
      .catch((err) => {
        throw toApiError(err)
      }),
    canonApi.listCharacters(bookId).then((c) => c.length),
    apiClient
      .get<BookPlanListResponse>(`/books/${bookId}/plans`)
      .then(({ data }) => data.plans)
      .catch((err) => {
        throw toApiError(err)
      }),
    booksApi.listBookScenes(bookId),
  ])
  return {
    sources,
    characters,
    outline: plans.some(
      (p) => p.kind === 'outline' && p.status === 'ready' && p.content.length > 0
    ),
    scenes: scenes.filter((s) => s.status === 'completed').length,
  }
}

/** The checklist itself: presentational, so it is testable without a network. */
export function GettingStartedChecklist({
  progress,
  onAction,
  onDismiss,
}: {
  progress: GettingStartedProgress
  onAction: (action: GettingStartedAction) => void
  onDismiss: () => void
}) {
  const done = GETTING_STARTED_STEPS.filter((s) => s.done(progress)).length
  const total = GETTING_STARTED_STEPS.length
  const next = GETTING_STARTED_STEPS.find((s) => !s.done(progress))

  return (
    <section
      aria-labelledby="getting-started-heading"
      className="mb-6 rounded-lg border border-primary-200 bg-primary-50 p-4"
      data-testid="getting-started"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2
            id="getting-started-heading"
            className="text-base font-semibold text-gray-900"
          >
            Getting started
          </h2>
          <p className="text-sm text-gray-600" data-testid="getting-started-progress">
            {done}/{total} done
          </p>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss getting started"
          className="rounded p-1 text-gray-400 hover:text-gray-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <ol className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {GETTING_STARTED_STEPS.map((step, i) => {
          const isDone = step.done(progress)
          const isNext = step === next
          return (
            <li
              key={step.key}
              data-done={isDone ? 'true' : 'false'}
              className={`flex flex-col gap-2 rounded-md border bg-white p-3 ${
                isNext ? 'border-primary-400' : 'border-gray-200'
              }`}
            >
              <div className="flex items-start gap-2">
                {isDone ? (
                  <CheckCircle2
                    className="mt-0.5 h-4 w-4 shrink-0 text-green-600"
                    aria-hidden="true"
                  />
                ) : (
                  <Circle
                    className="mt-0.5 h-4 w-4 shrink-0 text-gray-300"
                    aria-hidden="true"
                  />
                )}
                <div className="min-w-0">
                  <p
                    className={`text-sm font-medium ${
                      isDone ? 'text-gray-500 line-through' : 'text-gray-900'
                    }`}
                  >
                    {i + 1}. {step.label}
                    <span className="sr-only">{isDone ? ' (done)' : ' (to do)'}</span>
                  </p>
                  <p className="text-xs text-gray-500">{step.hint}</p>
                </div>
              </div>
              {!isDone && (
                <Button
                  size="sm"
                  variant={isNext ? 'primary' : 'outline'}
                  onClick={() => onAction(step.key)}
                  className="self-start"
                >
                  {step.action}
                </Button>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/** Loads the book's progress and shows the checklist until it is complete or
 *  dismissed. `refreshKey` refetches (the page passes the active tab, so a
 *  step done in one tab ticks when the author moves on). */
export default function GettingStarted({
  bookId,
  refreshKey,
  onAction,
}: {
  bookId: string
  refreshKey?: unknown
  onAction: (action: GettingStartedAction) => void
}) {
  const [progress, setProgress] = useState<GettingStartedProgress | null>(null)
  const [dismissed, setDismissed] = useState(true)

  useEffect(() => {
    setDismissed(readDismissed(bookId))
  }, [bookId])

  useEffect(() => {
    let live = true
    fetchGettingStartedProgress(bookId)
      .then((p) => {
        if (live) setProgress(p)
      })
      .catch(() => {
        // Unknown progress: show nothing rather than wrong ticks.
        if (live) setProgress(null)
      })
    return () => {
      live = false
    }
  }, [bookId, refreshKey])

  const dismiss = useCallback(() => {
    writeDismissed(bookId)
    setDismissed(true)
  }, [bookId])

  if (dismissed || !progress) return null
  if (GETTING_STARTED_STEPS.every((s) => s.done(progress))) return null
  return (
    <GettingStartedChecklist progress={progress} onAction={onAction} onDismiss={dismiss} />
  )
}
