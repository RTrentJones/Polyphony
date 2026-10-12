import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TrashPanel from '@/components/book/TrashPanel'
import type { TrashItem } from '@/lib/types'

const trashApi = vi.hoisted(() => ({
  list: vi.fn(),
  restore: vi.fn(),
  purge: vi.fn(),
}))
vi.mock('@/lib/trash-api', () => ({ trashApi }))

const milo: TrashItem = {
  type: 'character',
  id: 'c-1',
  name: 'Milo Voss',
  deleted_at: new Date().toISOString(),
  restores: { voice_samples: 12 },
  summary: 'the character bible and 12 voice samples',
}
const chapter: TrashItem = {
  type: 'chapter',
  id: 'ch-1',
  name: 'The Vault',
  deleted_at: new Date().toISOString(),
  restores: { scenes: 3 },
  summary: 'the chapter and 3 scenes',
}

function renderPanel() {
  const props = { bookId: 'b', addToast: vi.fn(), onChanged: vi.fn() }
  render(<TrashPanel {...props} />)
  return props
}

describe('TrashPanel (SPEC-13)', () => {
  beforeEach(() => {
    trashApi.list.mockReset()
    trashApi.restore.mockReset()
    trashApi.purge.mockReset()
  })

  it('lists what restoring each item brings back', async () => {
    trashApi.list.mockResolvedValue([milo, chapter])
    renderPanel()
    expect(await screen.findByText('Milo Voss')).toBeInTheDocument()
    expect(screen.getByText(/12 voice samples/)).toBeInTheDocument()
    expect(screen.getByText(/the chapter and 3 scenes/)).toBeInTheDocument()
  })

  it('says so when the Trash is empty', async () => {
    trashApi.list.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText('The Trash is empty.')).toBeInTheDocument()
  })

  it('restores, then refreshes the list and the book', async () => {
    trashApi.list.mockResolvedValueOnce([milo]).mockResolvedValueOnce([])
    trashApi.restore.mockResolvedValue(undefined)
    const props = renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Restore Milo Voss' }))
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled())
    expect(trashApi.restore).toHaveBeenCalledWith('b', 'character', 'c-1')
    expect(await screen.findByText('The Trash is empty.')).toBeInTheDocument()
  })

  it('shows the conflict a 409 names, and changes nothing', async () => {
    trashApi.list.mockResolvedValue([milo])
    trashApi.restore.mockRejectedValue({
      status: 409,
      message: 'conflict',
      detail: {
        message: "A character named 'Milo Voss' already exists in this book.",
        conflict: { type: 'character', id: 'c-2', name: 'Milo Voss' },
      },
    })
    const props = renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Restore Milo Voss' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "A character named 'Milo Voss' already exists"
    )
    expect(props.onChanged).not.toHaveBeenCalled()
    expect(screen.getByText('Milo Voss')).toBeInTheDocument()
  })

  it('purges only after the name is typed out exactly', async () => {
    trashApi.list.mockResolvedValueOnce([milo]).mockResolvedValueOnce([])
    trashApi.purge.mockResolvedValue(undefined)
    renderPanel()
    fireEvent.click(
      await screen.findByRole('button', { name: 'Delete Milo Voss forever' })
    )
    const confirm = screen.getByRole('button', { name: 'Delete forever' })
    expect(confirm).toBeDisabled()

    const input = screen.getByLabelText('Type the name to confirm')
    fireEvent.change(input, { target: { value: 'milo voss' } })
    expect(confirm).toBeDisabled()
    fireEvent.click(confirm)
    expect(trashApi.purge).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'Milo Voss' } })
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(trashApi.purge).toHaveBeenCalledWith('b', 'character', 'c-1'))
    expect(await screen.findByText('The Trash is empty.')).toBeInTheDocument()
  })
})
