import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({}),
  usePlanningStore: () => ({}),
}))

import { PremiseCheckpoint } from '@/components/book/OutlinePanel'
import type { BookPlan } from '@/lib/types'

const plan: BookPlan = {
  id: 'plan-1',
  book_id: 'book-1',
  kind: 'outline',
  content: [],
  status: 'awaiting_confirmation',
  premise: {
    premise_restated: 'A lich audits the afterlife.',
    central_conflict: 'Milo against Aeon Holdings.',
  },
  updated_at: null,
}

describe('PremiseCheckpoint (SPEC-10)', () => {
  it('shows the restated premise and confirms it unchanged', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(<PremiseCheckpoint plan={plan} onConfirm={onConfirm} />)

    expect(screen.getByText('A lich audits the afterlife.')).toBeInTheDocument()
    expect(screen.getByText('Milo against Aeon Holdings.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /looks right/i }))
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith({}))
  })

  it('sends the edited premise as authoritative', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(<PremiseCheckpoint plan={plan} onConfirm={onConfirm} />)

    fireEvent.click(screen.getByRole('button', { name: /edit and continue/i }))
    fireEvent.change(screen.getByLabelText('Premise'), {
      target: { value: 'A lich audits the living.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /continue with my edits/i }))
    await waitFor(() =>
      expect(onConfirm).toHaveBeenCalledWith({
        premise_restated: 'A lich audits the living.',
        central_conflict: 'Milo against Aeon Holdings.',
      })
    )
  })
})
