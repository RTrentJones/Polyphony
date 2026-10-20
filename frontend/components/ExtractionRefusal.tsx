/**
 * The preflight refusal of a whole-source extraction (SPEC-09): why it was
 * refused, and the "first N chapters" alternative. Shared by the add-source
 * dialog and the Sources panel's Extract control so both read the same.
 */

import Button from './Button'
import type { ApiError, ExtractionPreflight } from '@/lib/types'

/** The refused preflight carried by a 429 from POST .../extract, if any. */
export function refusedPreflight(err: unknown): ExtractionPreflight | null {
  const detail = (err as ApiError | undefined)?.detail as
    | { preflight?: ExtractionPreflight }
    | undefined
  const p = detail && typeof detail === 'object' ? detail.preflight : undefined
  return p && p.fits === false ? p : null
}

export default function ExtractionRefusal({
  preflight: p,
  saved = false,
  busy = false,
  compact = false,
  onExtractOpening,
  onDismiss,
}: {
  preflight: ExtractionPreflight
  /** Lead with "Your source is saved" (the add-source flow). */
  saved?: boolean
  busy?: boolean
  compact?: boolean
  onExtractOpening: () => void
  onDismiss: () => void
}) {
  return (
    <>
      <p className={compact ? 'text-xs text-gray-700' : 'text-sm text-gray-700'}>
        {saved ? 'Your source is saved, but extracting' : 'Extracting'} all of it
        would take about {p.estimated_tokens.toLocaleString()} tokens over{' '}
        {p.windows} parts ({p.requests} AI requests) — more than the{' '}
        {p.capacity_tokens.toLocaleString()} available before the job would give
        up waiting for quota.
      </p>
      <div
        className={
          compact ? 'flex items-center gap-2' : 'flex items-center space-x-3 pt-2'
        }
      >
        <Button
          variant="outline"
          size={compact ? 'sm' : undefined}
          fullWidth={!compact}
          onClick={onDismiss}
        >
          Not now
        </Button>
        {p.max_chapters ? (
          <Button
            size={compact ? 'sm' : undefined}
            fullWidth={!compact}
            onClick={onExtractOpening}
            isLoading={busy}
          >
            Extract the first {p.max_chapters} chapters
          </Button>
        ) : null}
      </div>
    </>
  )
}
