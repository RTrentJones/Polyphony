import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AddSourceModal, {
  ACCEPTED_EXTENSIONS,
  DOC_UNSUPPORTED_MESSAGE,
  formatsLabel,
} from '@/components/AddSourceModal'

const { get, uploadSource, push } = vi.hoisted(() => ({
  get: vi.fn(),
  uploadSource: vi.fn(),
  push: vi.fn(),
}))
vi.mock('@/lib/api-client', () => ({ default: { get } }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }))
vi.mock('@/lib/store', () => ({
  useSourceStore: () => ({ uploadSource, pasteSource: vi.fn() }),
  useExtractionStore: () => ({ startExtraction: vi.fn() }),
}))

function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector('input[type="file"]')
  if (!input) throw new Error('no file input')
  return input as HTMLInputElement
}

function pick(input: HTMLInputElement, name: string) {
  const file = new File(['hello'], name, { type: 'application/octet-stream' })
  fireEvent.change(input, { target: { files: [file] } })
}

describe('AddSourceModal upload types (B14, SPEC-21)', () => {
  beforeEach(() => {
    get.mockReset()
    uploadSource.mockReset()
  })

  it('takes the accepted list and size from GET /sources/config', async () => {
    get.mockResolvedValue({
      data: { accepted_extensions: ['.txt', '.pdf'], max_bytes: 2 * 1024 * 1024 },
    })
    const { container } = render(<AddSourceModal bookId="b" onClose={vi.fn()} />)
    await waitFor(() => expect(fileInput(container).accept).toBe('.txt,.pdf'))
    expect(get).toHaveBeenCalledWith('/sources/config')
    expect(screen.getByText(/Supported formats: TXT, PDF \(max 2/)).toBeInTheDocument()
  })

  it('falls back to the server list when the config call fails', async () => {
    get.mockRejectedValue(new Error('offline'))
    const { container } = render(<AddSourceModal bookId="b" onClose={vi.fn()} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(fileInput(container).accept).toBe(ACCEPTED_EXTENSIONS.join(','))
    expect(fileInput(container).accept).not.toContain('.doc,')
    expect(screen.getByText(/TXT, DOCX, PDF, HTML, HTM/)).toBeInTheDocument()
  })

  it('a .doc gets the "save as .docx" fix and cannot be uploaded', async () => {
    get.mockResolvedValue({
      data: { accepted_extensions: ACCEPTED_EXTENSIONS, max_bytes: 1024 * 1024 },
    })
    const { container } = render(<AddSourceModal bookId="b" onClose={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(/title for your source/), {
      target: { value: 'Old draft' },
    })
    pick(fileInput(container), 'draft.doc')
    expect(await screen.findByText(DOC_UNSUPPORTED_MESSAGE)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
    expect(uploadSource).not.toHaveBeenCalled()

    // Picking a supported file clears the hint.
    pick(fileInput(container), 'draft.docx')
    await waitFor(() =>
      expect(screen.queryByText(DOC_UNSUPPORTED_MESSAGE)).not.toBeInTheDocument()
    )
    expect(screen.getByRole('button', { name: 'Upload' })).not.toBeDisabled()
  })

  it('labels formats for the helper text', () => {
    expect(formatsLabel(['.txt', '.docx'])).toBe('TXT, DOCX')
  })
})
