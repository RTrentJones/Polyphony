'use client'

/**
 * The one cast chip control and the one POV-lock rule (R-2, SPEC-35): the
 * generate modal and the beat editor both pick a cast with it.
 */

import type { BookChapter, Character } from '@/lib/types'

/** The chapter's POV character when it is in the cast, else null. The server
 *  puts that POV in every scene (with_pov), so its chip stays on. */
export function chapterPovLock(chapter: BookChapter | undefined, castIds: string[]): string | null {
  const pov = chapter?.pov_character_id
  return pov && castIds.includes(pov) ? pov : null
}

export default function CastChips({
  cast,
  selected,
  locked,
  onToggle,
  label,
}: {
  cast: Character[]
  selected: string[]
  locked: string | null
  /** Never called for `locked`. */
  onToggle: (id: string) => void
  label: string
}) {
  return (
    <div className="flex flex-wrap gap-2" data-testid="cast-picker" role="group" aria-label={label}>
      {cast.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => c.id !== locked && onToggle(c.id)}
          aria-pressed={selected.includes(c.id)}
          aria-disabled={c.id === locked || undefined}
          className={`px-3 py-1 text-sm rounded-full border transition-colors ${
            selected.includes(c.id)
              ? 'border-primary-500 bg-primary-50 text-primary-700'
              : 'border-gray-300 text-gray-700 hover:border-primary-300'
          }`}
        >
          {c.name}
          {c.id === locked || c.role ? (
            <span className="ml-1 text-xs text-gray-500">
              · {c.id === locked ? 'point of view for this chapter' : c.role}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}
