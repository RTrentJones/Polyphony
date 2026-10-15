'use client'

/**
 * AI revision from the scene editor (SPEC-25, backend SPEC-19).
 *
 * "Revise selection…" and "Redo beat N" ask for a revision of a span of the
 * SAVED draft; the job's result is a proposal, shown as a side-by-side word
 * diff with Accept and Discard.
 *
 * - Offsets go to the server as Python code points into the draft (the raw
 *   editor text, edge whitespace included), converted from the textarea's
 *   UTF-16 selection.
 * - Every request carries `base_text_hash` (SHA-256 of the UTF-8 draft) and,
 *   when the client knows it, `selected_text`; a stale view is a 409, shown
 *   with the server's reason.
 * - Accept: the server writes the draft, so the editor takes the proposal as
 *   its saved text (not dirty). Discard touches nothing but the proposal.
 */

import { useCallback, useEffect, useState, type RefObject } from 'react'
import { Check, Sparkles, X } from 'lucide-react'

import Button from '@/components/Button'
import DiffView from '@/components/editor/DiffView'
import { useJob } from '@/components/useJob'
import {
  reviseApi,
  reviseErrorMessage,
  type ReviseRequest,
  type RevisionProposal,
} from '@/lib/revise-api'
import { selectionToDraftRange, sha256Hex } from '@/lib/text-offsets'
import type { Job } from '@/lib/types'
import { voiceApi } from '@/lib/voice-api'

type Target =
  | { kind: 'range'; start: number; end: number; text: string }
  | { kind: 'beat'; beatIndex: number; description: string | null }
  | { kind: 'scene' }

interface BeatOption {
  beat_index: number
  description: string | null
}

export interface RevisePanelProps {
  sceneId: string
  /** The editor's text, and whether it differs from the saved draft. */
  content: string
  isDirty: boolean
  /** Only a settled scene can be revised (the server refuses a generating one). */
  canRevise: boolean
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** The server saved `content` as the draft (an accepted proposal). */
  onAccepted: (content: string, updatedAt: string | null) => void
  onNotice?: (message: string, type: 'success' | 'info' | 'error') => void
}

function targetLabel(target: Target): string {
  if (target.kind === 'beat') return `Beat ${target.beatIndex + 1}`
  if (target.kind === 'scene') return 'The whole scene'
  return 'Selection'
}

export default function RevisePanel({
  sceneId,
  content,
  isDirty,
  canRevise,
  textareaRef,
  onAccepted,
  onNotice,
}: RevisePanelProps) {
  const [target, setTarget] = useState<Target | null>(null)
  const [instruction, setInstruction] = useState('')
  const [beats, setBeats] = useState<BeatOption[] | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [proposal, setProposal] = useState<RevisionProposal | null>(null)
  /** The draft the proposal was written against (the diff's left side). */
  const [baseDraft, setBaseDraft] = useState('')

  // A proposal still awaiting a decision (e.g. the page was reloaded while the
  // job ran) is offered again, if it still applies to the draft.
  useEffect(() => {
    if (!sceneId) return
    let cancelled = false
    reviseApi
      .proposals(sceneId)
      .then((data) => {
        if (cancelled) return
        const pending = data.proposals.find((p) => p.status === 'proposed' && !p.stale)
        if (pending) {
          setBaseDraft(data.draft)
          setProposal(pending)
        }
      })
      .catch(() => {
        // proposals are optional on load; the panel still works
      })
    return () => {
      cancelled = true
    }
  }, [sceneId])

  const loadBeats = useCallback(async () => {
    if (beats !== null) return
    try {
      const g = await voiceApi.sceneGrounding(sceneId)
      setBeats(g.beats.map((b) => ({ beat_index: b.beat_index, description: b.description })))
    } catch {
      setBeats([])
    }
  }, [beats, sceneId])

  useEffect(() => {
    if (canRevise) loadBeats()
  }, [canRevise, loadBeats])

  const onSettled = useCallback(
    async (job: Job) => {
      setJobId(null)
      if (job.status !== 'succeeded') {
        setError(
          job.status === 'cancelled'
            ? 'The revision was cancelled.'
            : job.error || 'The revision failed; nothing was changed.'
        )
        return
      }
      try {
        const data = await reviseApi.proposals(sceneId)
        const found =
          data.proposals.find((p) => p.job_id === job.id) ??
          data.proposals.find((p) => p.status === 'proposed')
        if (!found) {
          setError('The revision finished but left no proposal.')
          return
        }
        setBaseDraft(data.draft)
        setProposal(found)
        setTarget(null)
      } catch (e: any) {
        setError(e?.message || 'Could not load the revision.')
      }
    },
    [sceneId]
  )
  const { job } = useJob(jobId, { onSettled })

  const blocked = !canRevise
    ? 'The scene is still being generated; revise it once it is done.'
    : isDirty
    ? 'Save your edits first: AI revision works on the stored draft.'
    : null
  const busy = submitting || jobId !== null

  const startSelection = () => {
    setError(null)
    const el = textareaRef.current
    const picked = el ? selectionToDraftRange(content, el.selectionStart, el.selectionEnd) : null
    if (!picked) {
      setError('Select the words to revise in the scene text first.')
      return
    }
    setTarget({ kind: 'range', start: picked.start, end: picked.end, text: picked.text })
  }

  const startBeat = (beat: BeatOption) => {
    setError(null)
    setTarget({ kind: 'beat', beatIndex: beat.beat_index, description: beat.description })
  }

  const submit = async () => {
    if (!target || !instruction.trim() || busy || blocked) return
    setError(null)
    setSubmitting(true)
    try {
      const request: ReviseRequest = {
        instruction: instruction.trim(),
        base_text_hash: await sha256Hex(content),
      }
      if (target.kind === 'range') {
        request.range = { start: target.start, end: target.end }
        request.selected_text = target.text
      } else if (target.kind === 'beat') {
        // The server locates the beat's own prose; the hash guards the rest.
        request.beat_index = target.beatIndex
      } else {
        request.selected_text = content
      }
      const r = await reviseApi.revise(sceneId, request)
      if (r.deduplicated) {
        onNotice?.('A revision of this scene is already running; showing that one.', 'info')
      }
      setJobId(r.job_id)
    } catch (e: any) {
      setError(reviseErrorMessage(e))
    } finally {
      setSubmitting(false)
    }
  }

  const accept = async () => {
    if (!proposal || deciding) return
    if (
      isDirty &&
      !window.confirm('Accepting replaces your unsaved changes in the editor. Continue?')
    ) {
      return
    }
    setDeciding(true)
    setError(null)
    try {
      const r = await reviseApi.accept(sceneId, proposal.id)
      onAccepted(proposal.content, r.updated_at ?? null)
      setProposal(null)
      setInstruction('')
      onNotice?.('Revision accepted; the previous draft is in Revisions.', 'success')
    } catch (e: any) {
      setError(reviseErrorMessage(e))
    } finally {
      setDeciding(false)
    }
  }

  const discard = async () => {
    if (!proposal || deciding) return
    setDeciding(true)
    setError(null)
    try {
      await reviseApi.discard(sceneId, proposal.id)
      setProposal(null)
      onNotice?.('Revision discarded; the scene is unchanged.', 'info')
    } catch (e: any) {
      setError(reviseErrorMessage(e))
    } finally {
      setDeciding(false)
    }
  }

  return (
    <section
      aria-label="AI revision"
      data-testid="revise-panel"
      className="mt-4 rounded-lg border border-gray-200 bg-white p-4"
    >
      <h2 className="flex items-center text-sm font-semibold text-gray-900 mb-2">
        <Sparkles className="h-4 w-4 mr-1.5 text-primary-600" />
        Revise with AI
      </h2>

      {error && (
        <div role="alert" className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {proposal ? (
        <div data-testid="revise-review">
          <p className="mb-2 text-sm text-gray-600">
            {proposal.instruction ? <>&ldquo;{proposal.instruction}&rdquo; </> : null}
            {proposal.beat_index !== null && proposal.beat_index !== undefined
              ? `(beat ${proposal.beat_index + 1})`
              : null}
          </p>
          {proposal.warnings.length > 0 && (
            <ul className="mb-2 list-disc pl-5 text-xs text-yellow-800">
              {proposal.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          <DiffView before={baseDraft} after={proposal.content} />
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" onClick={accept} isLoading={deciding} disabled={deciding}>
              <Check className="h-4 w-4 mr-1" />
              Accept
            </Button>
            <Button variant="outline" size="sm" onClick={discard} disabled={deciding}>
              <X className="h-4 w-4 mr-1" />
              Discard
            </Button>
          </div>
        </div>
      ) : (
        <>
          {blocked && <p className="mb-2 text-sm text-gray-500">{blocked}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={startSelection}
              disabled={!!blocked || busy}
            >
              Revise selection…
            </Button>
            {(beats ?? []).map((b) => (
              <Button
                key={b.beat_index}
                variant="outline"
                size="sm"
                onClick={() => startBeat(b)}
                disabled={!!blocked || busy}
                title={b.description ?? undefined}
              >
                Redo beat {b.beat_index + 1}
              </Button>
            ))}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setError(null)
                setTarget({ kind: 'scene' })
              }}
              disabled={!!blocked || busy}
            >
              Whole scene…
            </Button>
          </div>

          {target && (
            <div className="mt-3 space-y-2" data-testid="revise-form">
              <p className="text-xs text-gray-500">
                {targetLabel(target)}
                {target.kind === 'range' && (
                  <span className="block mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-gray-700">
                    {target.text}
                  </span>
                )}
                {target.kind === 'beat' && target.description && (
                  <span className="block mt-1 text-gray-700">{target.description}</span>
                )}
              </p>
              <label className="block text-sm font-medium text-gray-700" htmlFor="revise-instruction">
                What should change?
              </label>
              <textarea
                id="revise-instruction"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                maxLength={2000}
                rows={2}
                placeholder="e.g. Make Zara colder here"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={submit}
                  isLoading={busy}
                  disabled={busy || !!blocked || !instruction.trim()}
                >
                  Ask for a revision
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setTarget(null)}
                  disabled={busy}
                >
                  Cancel
                </Button>
                {jobId && (
                  <span className="text-xs text-gray-500" role="status">
                    {job?.status === 'paused'
                      ? 'Waiting for AI quota…'
                      : 'Writing the revision…'}
                  </span>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
