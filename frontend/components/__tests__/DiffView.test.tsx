import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import DiffView, { wordDiff } from '@/components/editor/DiffView'

describe('wordDiff (SPEC-25)', () => {
  it('marks the inserted and deleted words and keeps the rest', () => {
    const parts = wordDiff('Zara smiled warmly at him.', 'Zara stared coldly at him.')
    expect(parts).toEqual([
      { op: 'equal', text: 'Zara ' },
      { op: 'delete', text: 'smiled warmly' },
      { op: 'insert', text: 'stared coldly' },
      { op: 'equal', text: ' at him.' },
    ])
  })

  it('rebuilds both texts exactly from its parts', () => {
    const before = 'One.\n\nTwo  three four.\nFive'
    const after = 'One.\n\nTwo four, and six.\nFive'
    const parts = wordDiff(before, after)
    expect(parts.filter((p) => p.op !== 'insert').map((p) => p.text).join('')).toBe(before)
    expect(parts.filter((p) => p.op !== 'delete').map((p) => p.text).join('')).toBe(after)
  })

  it('reports identical texts as one equal part', () => {
    expect(wordDiff('Same text.', 'Same text.')).toEqual([{ op: 'equal', text: 'Same text.' }])
  })
})

describe('DiffView', () => {
  it('shows deletions on the left and insertions on the right', () => {
    render(<DiffView before="Zara smiled at him." after="Zara glared at him." />)
    const left = screen.getByRole('region', { name: 'Current draft' })
    const right = screen.getByRole('region', { name: 'Proposed revision' })
    expect(within(left).getByTestId('diff-delete')).toHaveTextContent('smiled')
    expect(within(left).queryByTestId('diff-insert')).toBeNull()
    expect(within(right).getByTestId('diff-insert')).toHaveTextContent('glared')
    expect(within(right).queryByTestId('diff-delete')).toBeNull()
    expect(left).toHaveTextContent('Zara smiled at him.')
    expect(right).toHaveTextContent('Zara glared at him.')
  })
})
