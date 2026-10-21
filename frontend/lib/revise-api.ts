/**
 * AI revision of a scene (SPEC-19 backend, SPEC-25 UI).
 *
 * Backend: app/api/revise.py, mounted at /api/v1/books.
 * - POST /books/scenes/{id}/revise enqueues a `revise_scene` job for a range
 *   (code points into the server draft), a beat, or the whole scene.
 * - GET  /books/scenes/{id}/proposals lists the proposals (newest first) with
 *   the current draft and its hash. Proposals are NOT in /revisions.
 * - POST /books/scenes/{id}/revisions/{rid}/accept makes a proposal the draft
 *   (the previous draft is snapshotted; 409 if the draft moved on).
 * - POST /books/scenes/{id}/revisions/{rid}/discard marks it discarded.
 *
 * The types live here, not in lib/types.ts (wave-2 ownership).
 */

import apiClient, { toApiError } from './api-client'
import type { Estimate } from './usage-api'

export type ProposalStatus = 'proposed' | 'accepted' | 'discarded'

export interface RevisionProposal {
  id: string
  scene_id: string
  /** The whole proposed draft: the untouched text plus the revised span. */
  content: string
  word_count: number | null
  created_at: string | null
  instruction: string | null
  /** The span revised, [start, end) in code points of the base draft. */
  range: [number, number] | null
  /** Where the revised text sits in `content`. */
  revised_range: [number, number] | null
  beat_index: number | null
  job_id: string | null
  status: ProposalStatus | string | null
  warnings: string[]
  /** Written against a draft that has since changed: accept would 409. */
  stale: boolean
}

export interface ProposalsResponse {
  scene_id: string
  draft: string
  draft_hash: string
  proposals: RevisionProposal[]
}

/** Exactly one of `range` / `beat_index`, or neither for the whole scene. */
export interface ReviseRequest {
  instruction: string
  /** Python code points into the server draft, end exclusive. */
  range?: { start: number; end: number }
  beat_index?: number
  /** SHA-256 hex of the UTF-8 draft this request was made against. */
  base_text_hash: string
  /** The text at `range` (or the beat's / whole draft's text) as the client
   *  sees it; the server refuses a mismatch with 409. */
  selected_text?: string
  model?: string
}

export interface ReviseResponse {
  scene_id: string
  job_id: string
  status: string
  /** A revision of this scene was already running; this is that job. */
  deduplicated: boolean
  range?: [number, number]
  estimate?: Estimate
}

export interface DecisionResponse {
  id: string
  revision_id: string
  status: ProposalStatus
  word_count?: number
  updated_at?: string
}

async function call<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await fn()).data
  } catch (err) {
    throw toApiError(err)
  }
}

export const reviseApi = {
  revise(sceneId: string, request: ReviseRequest): Promise<ReviseResponse> {
    return call(() =>
      apiClient.post<ReviseResponse>(`/books/scenes/${sceneId}/revise`, request)
    )
  },

  proposals(sceneId: string): Promise<ProposalsResponse> {
    return call(() =>
      apiClient.get<ProposalsResponse>(`/books/scenes/${sceneId}/proposals`)
    )
  },

  accept(sceneId: string, revisionId: string): Promise<DecisionResponse> {
    return call(() =>
      apiClient.post<DecisionResponse>(
        `/books/scenes/${sceneId}/revisions/${revisionId}/accept`
      )
    )
  },

  discard(sceneId: string, revisionId: string): Promise<DecisionResponse> {
    return call(() =>
      apiClient.post<DecisionResponse>(
        `/books/scenes/${sceneId}/revisions/${revisionId}/discard`
      )
    )
  },
}

/** 409s that are not a stale view, and already say what to do. */
const SELF_EXPLAINED_409 = [/select the text instead/i, /still being generated/i, /already (accepted|discarded)/i]

/** What to show for a refused request. A 409 is (usually) a stale view: the
 *  server's own reason, plus what to do about it. */
export function reviseErrorMessage(err: { message?: string; status?: number }): string {
  const message = err?.message || 'The revision could not be requested.'
  if (err?.status === 409 && !SELF_EXPLAINED_409.some((re) => re.test(message))) {
    return `${message} The scene changed; reselect and try again.`
  }
  return message
}
