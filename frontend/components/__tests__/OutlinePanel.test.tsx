/** The Outline panel's History and promoted label (SPEC-38 §History). */
import { fireEvent, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BookChapter, BookPlan } from '@/lib/types'
import { bookValue, renderWithBook } from './bookContext'

const history = vi.hoisted(() => vi.fn())
vi.mock('@/components/VersionHistory', () => ({
  default: (props: Record<string, unknown>) => {
    history(props)
    return <div role="dialog" aria-label="History" />
  },
}))
vi.mock('@/lib/canon-store', () => ({ canonApi: {} }))
const plans = vi.hoisted(() => ({ current: [] as BookPlan[] }))
vi.mock('@/lib/books-store', () => ({
  usePlanningStore: () => ({
    plans: plans.current,
    fetchPlans: vi.fn().mockResolvedValue([]),
    generatePlan: vi.fn(),
    promoteNode: vi.fn(),
    promoteAll: vi.fn(),
    confirmPremise: vi.fn(),
  }),
}))

import OutlinePanel from '@/components/book/OutlinePanel'

const plan: BookPlan = {
  id: 'plan-1',
  book_id: 'book-1',
  kind: 'outline',
  status: 'ready',
  content: [
    { node_id: 'n-1', title: 'Intake', children: [] },
    { node_id: 'n-2', title: 'Lunch', children: [] },
  ],
  updated_at: null,
}
// `number` is the server's; the label never computes position + 1.
const promoted: BookChapter = {
  id: 'c-2', book_id: 'book-1', position: 7, number: 3, title: 'Lunch', plan_node_id: 'n-2',
}

describe('Outline history and promoted chapters (SPEC-38)', () => {
  beforeEach(() => {
    plans.current = [plan]
  })

  it('History opens the book_plan versions of this plan', () => {
    renderWithBook(<OutlinePanel addToast={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(history).toHaveBeenCalledWith(
      expect.objectContaining({ entityType: 'book_plan', entityId: 'plan-1' })
    )
  })

  it('History is disabled while a job is generating the plan', () => {
    plans.current = [{ ...plan, status: 'generating' }]
    renderWithBook(<OutlinePanel addToast={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'History' })).toBeDisabled()
  })

  it('a promoted node shows its chapter number', () => {
    renderWithBook(<OutlinePanel addToast={vi.fn()} />, bookValue({ chapters: [promoted] }))
    expect(screen.getByText('Promoted · chapter 3')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Promote to chapter' })).toHaveLength(1)
  })
})
