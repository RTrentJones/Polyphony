/**
 * Outline panel — the book's plans and their node trees.
 */

'use client'

import { useEffect, useRef, useState } from 'react'
import { ListTree, Plus, Wand2 } from 'lucide-react'
import Button from '@/components/Button'
import EstimateHint from '@/components/EstimateHint'
import type { Estimate } from '@/lib/usage-api'
import Card from '@/components/Card'
import JobStatusBadge from '@/components/JobStatusBadge'
import type { ToastType } from '@/components/Toast'
import { useBookStore, usePlanningStore } from '@/lib/books-store'
import { isActiveJob } from '@/lib/jobs'
import type { Job, PlanKind, PlanNode } from '@/lib/types'

function OutlineNodeView({ node }: { node: PlanNode }) {
  return (
    <div>
      <p className="font-medium text-gray-900">{node.title}</p>
      {node.summary && <p className="text-sm text-gray-600 mt-0.5">{node.summary}</p>}
      {node.children && node.children.length > 0 && (
        <ul className="mt-2 ml-4 space-y-1 border-l-2 border-gray-100 pl-3">
          {node.children.map((child, i) => (
            <li key={i}>
              <p className="text-sm text-gray-800">{child.title}</p>
              {child.summary && (
                <p className="text-xs text-gray-500">{child.summary}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function OutlinePanel({
  bookId,
  hasSynopsis,
  addToast,
}: {
  bookId: string
  hasSynopsis: boolean
  addToast: (message: string, type: ToastType) => void
}) {
  const { plans, fetchPlans, generatePlan, promoteNode } = usePlanningStore()
  const { fetchBook } = useBookStore()

  const [kind, setKind] = useState<PlanKind>('outline')
  const [chaptersTarget, setChaptersTarget] = useState(12)
  const [est, setEst] = useState<Estimate | null>(null)
  const [generating, setGenerating] = useState(false)
  const [promotingIndex, setPromotingIndex] = useState<number | null>(null)
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

  // The job driving the plan (SPEC-07): from the generate response, or from
  // the plan list after a reload. JobStatusBadge polls it; when it settles we
  // refetch the plan and say how it ended.
  const [jobId, setJobId] = useState<string | null>(null)
  const activeJobId =
    jobId || (isActiveJob(plan?.job?.status) ? plan?.job?.job_id ?? null : null)
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
    if (job.status === 'succeeded') addToast(`${label} generated`, 'success')
    else if (job.status === 'cancelled') addToast(`${label} generation cancelled`, 'info')
    else addToast(p?.error || job.error || `${label} generation failed`, 'error')
  }

  const handlePromote = async (index: number, title: string) => {
    setPromotingIndex(index)
    try {
      await promoteNode(bookId, index)
      await fetchBook(bookId)
      addToast(`"${title}" promoted to chapter`, 'success')
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
        <div className="flex items-end gap-2">
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

      {generating ||
      plan?.status === 'generating' ||
      plan?.status === 'paused' ||
      activeJobId ? (
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
      ) : !plan || plan.content.length === 0 ? (
        <div className="text-center py-8">
          <ListTree className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">
            No {kind === 'outline' ? 'outline' : 'beat sheet'} yet
          </p>
        </div>
      ) : (
        <div className="space-y-3">
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
              key={index}
              className="flex items-start gap-3 p-3 rounded-lg border border-gray-200"
            >
              <span className="text-xs text-gray-400 mt-1 w-6 text-right">
                {index + 1}
              </span>
              <div className="flex-1 min-w-0">
                <OutlineNodeView node={node} />
              </div>
              {kind === 'outline' && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handlePromote(index, node.title)}
                  isLoading={promotingIndex === index}
                  disabled={promotingIndex !== null}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Promote to chapter
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
