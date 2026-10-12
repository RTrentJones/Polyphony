import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import KeyValueEditor from '@/components/KeyValueEditor'

describe('KeyValueEditor', () => {
  it('renders one row per entry', () => {
    render(
      <KeyValueEditor
        label="Traits"
        value={{ brave: 'very', loyal: 'always' }}
        onChange={() => {}}
      />
    )
    expect(screen.getByDisplayValue('brave')).toBeInTheDocument()
    expect(screen.getByDisplayValue('always')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Remove' })).toHaveLength(2)
  })

  it('emits the edited map, dropping blank-keyed rows', () => {
    const onChange = vi.fn()
    render(<KeyValueEditor label="Traits" value={{ brave: 'very' }} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: /add traits/i }))
    // A new blank row is not emitted until it has a key.
    expect(onChange).toHaveBeenLastCalledWith({ brave: 'very' })

    const keys = screen.getAllByPlaceholderText('Key')
    fireEvent.change(keys[1], { target: { value: '  wry ' } })
    expect(onChange).toHaveBeenLastCalledWith({ brave: 'very', wry: '' })

    const values = screen.getAllByPlaceholderText('Value')
    fireEvent.change(values[1], { target: { value: 'dry' } })
    expect(onChange).toHaveBeenLastCalledWith({ brave: 'very', wry: 'dry' })
  })

  it('removes a row', () => {
    const onChange = vi.fn()
    render(
      <KeyValueEditor label="Traits" value={{ a: '1', b: '2' }} onChange={onChange} />
    )
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0])
    expect(onChange).toHaveBeenLastCalledWith({ b: '2' })
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument()
  })

  it('re-seeds when a different object is passed in', () => {
    const { rerender } = render(
      <KeyValueEditor label="Traits" value={{ a: '1' }} onChange={() => {}} />
    )
    rerender(<KeyValueEditor label="Traits" value={{ z: '9' }} onChange={() => {}} />)
    expect(screen.queryByDisplayValue('a')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('z')).toBeInTheDocument()
  })
})
