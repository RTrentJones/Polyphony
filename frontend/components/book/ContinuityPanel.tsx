/**
 * Continuity panel — continuity reports and their findings.
 */

'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import JobStatusBadge from '@/components/JobStatusBadge'
import Loading from '@/components/Loading'
import type { ToastType } from '@/components/Toast'
import { usePlanningStore } from '@/lib/books-store'
import { isActiveJob } from '@/lib/jobs'
import type { BookChapter, ContinuityFinding, ContinuityReport } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'
import { SEVERITY_STYLES, SEVERITY_ORDER } from '@/components/book/styles'

/** In flight with no live job to watch: the panel polls the list itself. A
 *  report whose job is live is watched by its JobStatusBadge instead. */
const needsListPoll = (r: ContinuityReport) =>
  (r.status === 'processing' || r.status === 'paused') && !isActiveJob(r.job?.status)

function FindingRow({ finding }: { finding: ContinuityFinding }) {
  return (
    <div
      className={`rounded-lg border p-3 ${
        SEVERITY_STYLES[finding.severity] || SEVERITY_STYLES.minor
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-semibold uppercase tracking-wide">
          {finding.severity}
        </span>
        <span className="text-xs opacity-70">{finding.type}</span>
      </div>
      <p className="text-sm">{finding.detail}</p>
      {finding.refs && <p className="text-xs mt-1 opacity-70">Ref: {finding.refs}</p>}
    </div>
  )
}

export default function ContinuityPanel({
  bookId,
  chapters,
  addToast,
}: {
  bookId: string
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { reports, fetchContinuityReports, runContinuityCheck } = usePlanningStore()

  const [scopeChapterId, setScopeChapterId] = useState('')
  const [running, setRunning] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const schedulePoll = useCallback(() => {
    if (pollTimer.current) clearTimeout(pollTimer.current)
    pollTimer.current = setTimeout(async () => {
      try {
        const latest = await fetchContinuityReports(bookId)
        if (latest.some(needsListPoll)) {
          schedulePoll()
        }
      } catch {
        schedulePoll()
      }
    }, 5000)
  }, [bookId, fetchContinuityReports])

  useEffect(() => {
    fetchContinuityReports(bookId)
      .then((latest) => {
        if (latest.some(needsListPoll)) schedulePoll()
      })
      .catch((err: any) => {
        addToast(err.message || 'Failed to load continuity reports', 'error')
      })
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current)
    }
  }, [bookId, fetchContinuityReports, schedulePoll, addToast])

  const handleRun = async () => {
    setRunning(true)
    try {
      await runContinuityCheck(bookId, scopeChapterId || undefined)
      addToast('Continuity check started', 'info')
      schedulePoll()
    } catch (err: any) {
      addToast(err.message || 'Failed to start continuity check', 'error')
    } finally {
      setRunning(false)
    }
  }

  const chapterTitle = (id: string | null) =>
    id ? chapters.find((c) => c.id === id)?.title ?? 'Unknown chapter' : null

  const sortedFindings = (findings: ContinuityFinding[]) =>
    [...findings].sort(
      (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
    )

  return (
    <Card>
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Continuity</h2>
          <p className="text-sm text-gray-500">
            Check the prose for contradictions against the bible, threads, and
            chapter summaries
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Scope
            </label>
            <select
              value={scopeChapterId}
              onChange={(e) => setScopeChapterId(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent max-w-[16rem]"
            >
              <option value="">Whole book</option>
              {chapters.map((c) => (
                <option key={c.id} value={c.id}>
                  Chapter {c.position + 1}: {c.title}
                </option>
              ))}
            </select>
          </div>
          <Button size="sm" onClick={handleRun} isLoading={running}>
            <ShieldCheck className="h-4 w-4 mr-1" />
            Run Check
          </Button>
        </div>
      </div>

      {reports.length === 0 ? (
        <div className="text-center py-8">
          <ShieldCheck className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No continuity reports yet</p>
        </div>
      ) : (
        <div className="space-y-4">
          {reports.map((report) => (
            <div key={report.id} className="border border-gray-200 rounded-lg p-4">
              <div className="flex flex-wrap items-center gap-2 mb-3">
                {isActiveJob(report.job?.status) ? (
                  <JobStatusBadge
                    jobId={report.job?.job_id}
                    initial={report.job}
                    onSettled={() => {
                      fetchContinuityReports(bookId).catch(() => {})
                    }}
                  />
                ) : (
                <span
                  className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                    report.status === 'completed'
                      ? 'bg-green-100 text-green-700'
                      : report.status === 'processing' || report.status === 'paused'
                      ? 'bg-yellow-100 text-yellow-700'
                      : report.status === 'incomplete'
                      ? 'bg-amber-100 text-amber-800'
                      : report.status === 'cancelled'
                      ? 'bg-gray-100 text-gray-600'
                      : 'bg-red-100 text-red-700'
                  }`}
                >
                  {report.status}
                </span>
                )}
                <span className="text-sm font-medium text-gray-900">
                  {report.scope === 'chapter'
                    ? `Chapter: ${chapterTitle(report.chapter_id)}`
                    : 'Whole book'}
                </span>
                {report.created_at && (
                  <span className="text-xs text-gray-400">
                    {formatRelativeTime(new Date(report.created_at))}
                  </span>
                )}
                {typeof report.tokens_used === 'number' && (
                  <span className="text-xs text-gray-400">
                    {report.tokens_used.toLocaleString()} tokens
                  </span>
                )}
              </div>

              {report.status === 'incomplete' && report.coverage && (
                <p className="text-sm text-amber-700 mb-2" data-testid="coverage">
                  Checked {report.coverage.chunks_checked} of{' '}
                  {report.coverage.chunks_total} sections — the rest could not be
                  checked ({report.coverage.unchecked.join(', ')}). Run it again to
                  cover them.
                </p>
              )}
              {report.status === 'processing' ? (
                <Loading size="sm" text="Checking prose..." />
              ) : report.status === 'paused' ? (
                <p className="text-sm text-indigo-700">
                  Waiting for AI quota — the check resumes automatically.
                </p>
              ) : report.status === 'failed' ? (
                <p className="text-sm text-red-600">This check failed — try again.</p>
              ) : report.status === 'cancelled' ? (
                <p className="text-sm text-gray-600">This check was cancelled.</p>
              ) : !report.findings || report.findings.length === 0 ? (
                report.status === 'incomplete' ? (
                  <p className="text-sm text-gray-600">
                    No contradictions in the sections that were checked.
                  </p>
                ) : (
                <p className="text-sm text-green-700">
                  No contradictions found. Clean pass.
                </p>
                )
              ) : (
                <div className="space-y-2">
                  {sortedFindings(report.findings).map((finding, i) => (
                    <FindingRow key={i} finding={finding} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
