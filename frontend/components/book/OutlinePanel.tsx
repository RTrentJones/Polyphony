/**
 * Outline panel — the book's plans and their node trees. History restores an
 * earlier plan; a promoted node names its chapter (SPEC-38).
 */

'use client'

import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, History, ListTree, Plus, UserPlus, Wand2 } from 'lucide-react'
import Button from '@/components/Button'
import EstimateHint from '@/components/EstimateHint'
import type { Estimate } from '@/lib/usage-api'
import Card from '@/components/Card'
import JobStatusBadge from '@/components/JobStatusBadge'
import VersionHistory from '@/components/VersionHistory'
import type { ToastType } from '@/components/Toast'
import { usePlanningStore } from '@/lib/books-store'
import { canonApi } from '@/lib/canon-store'
import { useBook } from '@/components/book/BookProvider'
import { isActiveJob } from '@/lib/jobs'
import type {
  BookPlan,
  Job,
  PlanKind,
  PlanNode,
  SuggestedCharacter,
} from '@/lib/types'

function OutlineNodeView({ node }: { node: PlanNode }) {
  return (
    <div>
      <p className="font-medium text-gray-900">{node.title}</p>
      {(node.pov || node.act_index != null) && (
        <p className="text-xs text-gray-500">
          {node.act_index != null ? `Act ${node.act_index + 1}` : ''}
          {node.act_index != null && node.pov ? ' · ' : ''}
          {node.pov ? `POV: ${node.pov}` : ''}
        </p>
      )}
      {node.summary && <p className="text-sm text-gray-600 mt-0.5">{node.summary}</p>}
      {node.children && node.children.length > 0 && (
        <ul className="mt-2 ml-4 space-y-1 border-l-2 border-gray-100 pl-3">
          {node.children.map((child, i) => (
            <li key={i}>
              <p className="text-sm text-gray-800">{child.title}</p>
              {child.summary && (
                <p className="text-xs text-gray-500">{child.summary}</p>
              )}
              {(child.setting || child.tone) && (
                <p className="text-xs text-gray-400">
                  {[child.setting, child.tone].filter(Boolean).join(' · ')}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** People the outline names who are in the synopsis or canon but not the
 *  cast (SPEC-24 computes them, SPEC-27 offers them). One click adds each as a
 *  `minor` character, described by the synopsis sentence that names them, so
 *  scenes can cast them; `minor` never makes them a fidelity-gate principal. */
export function SuggestedCast({
  suggestions,
  onAdd,
}: {
  suggestions: SuggestedCharacter[]
  onAdd: (suggestion: SuggestedCharacter) => Promise<void>
}) {
  const [adding, setAdding] = useState<string | null>(null)
  if (suggestions.length === 0) return null

  const add = async (s: SuggestedCharacter) => {
    setAdding(s.name)
    try {
      await onAdd(s)
    } finally {
      setAdding(null)
    }
  }

  return (
    <section
      aria-labelledby="suggested-cast-heading"
      className="border border-sky-200 bg-sky-50 rounded-lg p-3"
      data-testid="suggested-cast"
    >
      <p id="suggested-cast-heading" className="text-sm font-medium text-sky-900">
        Suggested cast
      </p>
      <p className="text-xs text-sky-800 mb-2">
        The outline names {suggestions.length === 1 ? 'this person' : 'these people'}{' '}
        from your synopsis or canon. Add them to the cast so scenes can include
        them.
      </p>
      <ul className="flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <li key={s.name}>
            <Button
              size="sm"
              variant="outline"
              onClick={() => add(s)}
              isLoading={adding === s.name}
              disabled={adding !== null}
              title={s.synopsis_sentence || undefined}
              aria-label={`Add ${s.name} to cast`}
            >
              <UserPlus className="h-3.5 w-3.5 mr-1" />
              Add {s.name} to cast
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The premise the model understood, for the author to confirm or correct
 *  before chapters are built on it (SPEC-10). An edit is authoritative for
 *  the chapters stage. */
export function PremiseCheckpoint({
  plan,
  onConfirm,
}: {
  plan: BookPlan
  onConfirm: (edits: {
    premise_restated?: string
    central_conflict?: string
  }) => Promise<void>
}) {
  const premise = plan.premise ?? {}
  const [editing, setEditing] = useState(false)
  const [restated, setRestated] = useState(premise.premise_restated ?? '')
  const [conflict, setConflict] = useState(premise.central_conflict ?? '')
  const [busy, setBusy] = useState(false)

  const submit = async (withEdits: boolean) => {
    setBusy(true)
    try {
      await onConfirm(
        withEdits
          ? { premise_restated: restated, central_conflict: conflict }
          : {}
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="border border-primary-200 bg-primary-50 rounded-lg p-4 space-y-3"
      data-testid="premise-checkpoint"
    >
      <div>
        <p className="font-medium text-gray-900">Is this the book?</p>
        <p className="text-sm text-gray-600">
          This is the premise the outline will be built on. Confirm it, or
          correct it first; the chapters are not drafted until you do.
        </p>
      </div>
      {editing ? (
        <>
          <label className="block text-sm font-medium text-gray-700">
            Premise
            <textarea
              value={restated}
              onChange={(e) => setRestated(e.target.value)}
              rows={4}
              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </label>
          <label className="block text-sm font-medium text-gray-700">
            Central conflict
            <textarea
              value={conflict}
              onChange={(e) => setConflict(e.target.value)}
              rows={2}
              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </label>
        </>
      ) : (
        <>
          <div>
            <p className="text-xs font-medium uppercase text-gray-500">Premise</p>
            <p className="text-sm text-gray-800 whitespace-pre-wrap">
              {premise.premise_restated || '(none restated)'}
            </p>
          </div>
          {premise.central_conflict && (
            <div>
              <p className="text-xs font-medium uppercase text-gray-500">
                Central conflict
              </p>
              <p className="text-sm text-gray-800">{premise.central_conflict}</p>
            </div>
          )}
        </>
      )}
      <div className="flex gap-2">
        {editing ? (
          <>
            <Button size="sm" onClick={() => submit(true)} isLoading={busy}>
              Continue with my edits
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
              Discard edits
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" onClick={() => submit(false)} isLoading={busy}>
              <CheckCircle2 className="h-4 w-4 mr-1" />
              Looks right
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              Edit and continue
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

export default function OutlinePanel({
  addToast,
}: {
  addToast: (message: string, type: ToastType) => void
}) {
  const { bookId, book, chapters, refreshBook, refreshCharacters } = useBook()
  const hasSynopsis = !!book?.synopsis
  const { plans, fetchPlans, generatePlan, promoteNode, promoteAll, confirmPremise } =
    usePlanningStore()

  const [kind, setKind] = useState<PlanKind>('outline')
  const [chaptersTarget, setChaptersTarget] = useState(12)
  const [est, setEst] = useState<Estimate | null>(null)
  const [generating, setGenerating] = useState(false)
  const [promotingIndex, setPromotingIndex] = useState<number | null>(null)
  const [promotingAll, setPromotingAll] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const cancelledRef = useRef(false)

  useEffect(() => {
    fetchPlans(bookId).catch((err: any) => {
      addToast(err.message || 'Failed to load plans', 'error')
    })
  }, [bookId, fetchPlans, addToast])

  // Stop polling if the user navigates away mid-generation.
  useEffect(() => {
    cancelledRef.current = false
    return () => {
      cancelledRef.current = true
    }
  }, [])

  const plan = plans.find((p) => p.kind === kind)
  // The live chapter each outline node was promoted to, by its number (S9).
  const promotedAs = new Map(chapters.map((c) => [c.plan_node_id ?? '', c.number]))

  // The job driving the plan (SPEC-07): from the generate response, or from
  // the plan list after a reload. JobStatusBadge polls it; when it settles we
  // refetch the plan and say how it ended.
  const [jobId, setJobId] = useState<string | null>(null)
  const activeJobId =
    jobId || (isActiveJob(plan?.job?.status) ? plan?.job?.job_id ?? null : null)
  const busy =
    generating || plan?.status === 'generating' || plan?.status === 'paused' || !!activeJobId
  const label = kind === 'outline' ? 'Outline' : 'Beat sheet'

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      const res = await generatePlan(bookId, kind, chaptersTarget)
      if (res?.job_id) setJobId(res.job_id)
      await fetchPlans(bookId)
    } catch (err: any) {
      addToast(err.message || 'Failed to generate plan', 'error')
    } finally {
      if (!cancelledRef.current) setGenerating(false)
    }
  }

  const handleSettled = async (job: Job) => {
    if (cancelledRef.current) return
    setJobId(null)
    const latest = await fetchPlans(bookId).catch(() => [])
    const p = latest.find((pl) => pl.kind === kind)
    if (job.status === 'succeeded' && p?.status === 'awaiting_confirmation')
      addToast('Check the premise before the chapters are drafted', 'info')
    else if (job.status === 'succeeded') addToast(`${label} generated`, 'success')
    else if (job.status === 'cancelled') addToast(`${label} generation cancelled`, 'info')
    else addToast(p?.error || job.error || `${label} generation failed`, 'error')
  }

  const handleConfirm = async (edits: {
    premise_restated?: string
    central_conflict?: string
  }) => {
    if (!plan) return
    try {
      const res = await confirmPremise(bookId, plan.id, edits)
      if (res?.job_id) setJobId(res.job_id)
    } catch (err: any) {
      addToast(err.message || 'Failed to continue the outline', 'error')
    }
  }

  const handleAddSuggested = async (s: SuggestedCharacter) => {
    try {
      await canonApi.createCharacter(bookId, {
        name: s.name,
        role: 'minor',
        description: s.synopsis_sentence || undefined,
      })
    } catch (err: any) {
      addToast(err.message || `Failed to add ${s.name}`, 'error')
      return
    }
    // The suggestion is computed at read time, so the refetch drops the chip.
    await Promise.all([
      fetchPlans(bookId).catch(() => []),
      refreshCharacters().catch(() => []),
      refreshBook().catch(() => undefined),
    ])
    addToast(`${s.name} added — now castable`, 'success')
  }

  const handlePromoteAll = async () => {
    setPromotingAll(true)
    try {
      const res = await promoteAll(bookId)
      await refreshBook()
      addToast(
        res.created
          ? `${res.created} chapter${res.created === 1 ? '' : 's'} created from the outline`
          : 'Every outline node already has a chapter',
        'success'
      )
    } catch (err: any) {
      addToast(err.message || 'Failed to promote the outline', 'error')
    } finally {
      setPromotingAll(false)
    }
  }

  const handlePromote = async (index: number, node: PlanNode) => {
    setPromotingIndex(index)
    try {
      const res = await promoteNode(bookId, {
        nodeId: node.node_id,
        nodeIndex: index,
      })
      await refreshBook()
      addToast(
        res.created === false
          ? `"${node.title}" already has a chapter`
          : `"${node.title}" promoted to chapter`,
        'success'
      )
    } catch (err: any) {
      addToast(err.message || 'Failed to promote node', 'error')
    } finally {
      setPromotingIndex(null)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-4 mb-4">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Outline</h2>
          <p className="text-sm text-gray-500">
            Draft the book&apos;s structure, then promote nodes into chapters
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Kind
            </label>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as PlanKind)}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="outline">Outline</option>
              <option value="beat_sheet">Beat sheet</option>
            </select>
          </div>
          <div className="w-28">
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Chapters
            </label>
            <input
              type="number"
              min={3}
              max={40}
              value={chaptersTarget}
              onChange={(e) => setChaptersTarget(parseInt(e.target.value, 10) || 12)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>
          <Button
            size="sm"
            onClick={handleGenerate}
            isLoading={generating}
            disabled={est !== null && !est.allowed}
          >
            <Wand2 className="h-4 w-4 mr-1" />
            Generate {kind === 'outline' ? 'outline' : 'beat sheet'}
          </Button>
          {plan && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setHistoryOpen(true)}
              // A running job writes its nodes over a restore when it finishes.
              disabled={busy}
              title={busy ? 'Available when generation finishes' : undefined}
            >
              <History className="h-4 w-4 mr-1" />
              History
            </Button>
          )}
        </div>
      </div>

      {hasSynopsis && (
        <div className="mb-4">
          <EstimateHint
            request={{
              action: 'outline',
              book_id: bookId,
              mode: kind === 'outline' ? 'staged' : 'single',
            }}
            onEstimate={setEst}
          />
        </div>
      )}

      {!hasSynopsis && (
        <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg p-3">
          <p className="text-sm text-yellow-700">
            Add a synopsis to the book (edit the header above) before generating an
            outline.
          </p>
        </div>
      )}

      {busy ? (
        <div className="text-center py-8">
          <Wand2 className="h-10 w-10 text-primary-500 mx-auto mb-3 animate-pulse" />
          <p className="text-gray-800 font-medium">
            Generating {kind === 'outline' ? 'outline' : 'beat sheet'}…
          </p>
          <div className="mt-2 flex justify-center">
            <JobStatusBadge
              jobId={activeJobId}
              initial={plan?.job}
              onSettled={handleSettled}
            />
          </div>
          <p className="text-sm text-gray-500 mt-1">
            {plan?.stage ? `Stage: ${plan.stage}. ` : ''}This runs in the
            background and can take a few minutes.
          </p>
        </div>
      ) : plan?.status === 'failed' ? (
        <div className="text-center py-8">
          <p className="text-red-600 font-medium">Generation failed</p>
          {plan.error && (
            <p className="text-sm text-gray-600 mt-1">{plan.error}</p>
          )}
          <p className="text-sm text-gray-500 mt-2">
            Try again, or adjust the synopsis and canon first.
          </p>
        </div>
      ) : plan?.status === 'awaiting_confirmation' && plan.premise ? (
        <PremiseCheckpoint key={plan.id} plan={plan} onConfirm={handleConfirm} />
      ) : !plan || plan.content.length === 0 ? (
        <div className="text-center py-8">
          <ListTree className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No {kind === 'outline' ? 'outline' : 'beat sheet'} yet
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {kind === 'outline' && (
            <div className="flex justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={handlePromoteAll}
                isLoading={promotingAll}
                disabled={promotingIndex !== null}
              >
                <Plus className="h-3.5 w-3.5 mr-1" />
                Promote all to chapters
              </Button>
            </div>
          )}
          {kind === 'outline' && (
            <SuggestedCast
              suggestions={plan.suggested_characters ?? []}
              onAdd={handleAddSuggested}
            />
          )}
          {plan.warnings && plan.warnings.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
              <p className="text-sm text-amber-800 font-medium">
                Fidelity warnings
              </p>
              <ul className="list-disc list-inside text-sm text-amber-700 mt-1">
                {plan.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          {plan.content.map((node, index) => (
            <div
              key={node.node_id ?? index}
              className="flex items-start gap-3 p-3 rounded-lg border border-gray-200"
            >
              <span className="text-xs text-gray-400 mt-1 w-6 text-right">
                {index + 1}
              </span>
              <div className="flex-1 min-w-0">
                <OutlineNodeView node={node} />
              </div>
              {kind === 'outline' && node.node_id && promotedAs.has(node.node_id) ? (
                <span className="text-sm text-gray-600 mt-1">
                  Promoted · chapter {promotedAs.get(node.node_id)}
                </span>
              ) : kind === 'outline' && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handlePromote(index, node)}
                  isLoading={promotingIndex === index}
                  disabled={promotingIndex !== null || promotingAll}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Promote to chapter
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
      {historyOpen && plan && (
        <VersionHistory
          bookId={bookId}
          entityType="book_plan"
          entityId={plan.id}
          label={label}
          onClose={() => setHistoryOpen(false)}
          onRestored={() => fetchPlans(bookId).catch(() => [])}
        />
      )}
    </Card>
  )
}
