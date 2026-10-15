import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

import ExportDialog, { validateChapterRange } from '@/components/book/ExportDialog'
import { booksApi, exportQuery } from '@/lib/books-store'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api-client', () => ({
  default: { get },
  toApiError: (e: any) => e,
  normalizeSource: (s: any) => s,
}))

/** The query string axios builds from the params object. */
const qs = (params: object) =>
  new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])
  ).toString()

describe('exportQuery (B9 contract with GET /books/{id}/export)', () => {
  it('sends both switches and leaves an unset range out', () => {
    expect(
      qs(exportQuery({ format: 'md', includeSynopsis: false, skipEmpty: true }))
    ).toBe('format=md&include_synopsis=false&skip_empty=true')
  })

  it('adds a 1-based inclusive chapter range', () => {
    expect(
      qs(
        exportQuery({
          format: 'epub',
          includeSynopsis: true,
          skipEmpty: false,
          fromChapter: 3,
          toChapter: 5,
        })
      )
    ).toBe('format=epub&include_synopsis=true&skip_empty=false&from_chapter=3&to_chapter=5')
  })

  it('sends an open-ended range with only the bound that is set', () => {
    expect(
      exportQuery({
        format: 'docx',
        includeSynopsis: false,
        skipEmpty: true,
        fromChapter: null,
        toChapter: 2,
      })
    ).toEqual({ format: 'docx', include_synopsis: false, skip_empty: true, to_chapter: 2 })
  })
})

describe('booksApi.exportBook', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({ data: new Blob(['# x']), headers: {} })
    ;(URL as any).createObjectURL = vi.fn(() => 'blob:x')
    ;(URL as any).revokeObjectURL = vi.fn()
    // jsdom cannot follow the download link; the browser would.
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  it('passes the dialog options as query params', async () => {
    await booksApi.exportBook('b-1', 'md', 'Bored', {
      includeSynopsis: true,
      skipEmpty: true,
      fromChapter: 2,
      toChapter: null,
    })
    expect(get).toHaveBeenCalledWith('/books/b-1/export', {
      params: { format: 'md', include_synopsis: true, skip_empty: true, from_chapter: 2 },
      responseType: 'blob',
    })
  })

  it('still sends just the format when called without options', async () => {
    await booksApi.exportBook('b-1', 'docx', 'Bored')
    expect(get.mock.calls[0][1].params).toEqual({ format: 'docx' })
  })
})

describe('validateChapterRange', () => {
  it('treats blank as the whole book', () => {
    expect(validateChapterRange('', '', 6)).toEqual({ fromChapter: null, toChapter: null })
  })
  it('accepts a range inside the book', () => {
    expect(validateChapterRange('2', '4', 6)).toEqual({ fromChapter: 2, toChapter: 4 })
  })
  it('refuses from after to', () => {
    expect(validateChapterRange('5', '2', 6)).toHaveProperty('error')
  })
  it('refuses numbers outside the book or not whole', () => {
    for (const [f, t] of [['0', ''], ['', '7'], ['1.5', ''], ['-1', '']]) {
      expect(validateChapterRange(f, t, 6)).toHaveProperty('error')
    }
  })
})

describe('ExportDialog (B9)', () => {
  let exportBook: MockInstance<typeof booksApi.exportBook>

  beforeEach(() => {
    exportBook = vi.spyOn(booksApi, 'exportBook').mockResolvedValue(undefined)
  })
  afterEach(() => exportBook.mockRestore())

  function renderDialog(chapterCount = 6) {
    const props = {
      isOpen: true,
      onClose: vi.fn(),
      bookId: 'b-1',
      bookTitle: 'Bored to Undeath',
      chapterCount,
      onExported: vi.fn(),
    }
    render(<ExportDialog {...props} />)
    return props
  }

  it('defaults to Markdown, no synopsis, empty chapters skipped, every chapter', async () => {
    const props = renderDialog()
    expect(screen.getByRole('radio', { name: /Markdown/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Include synopsis/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Skip empty chapters/ })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(props.onClose).toHaveBeenCalled())
    expect(exportBook).toHaveBeenCalledWith('b-1', 'md', 'Bored to Undeath', {
      includeSynopsis: false,
      skipEmpty: true,
      fromChapter: null,
      toChapter: null,
    })
    expect(props.onExported).toHaveBeenCalledWith('md')
  })

  it('sends the chosen format, switches and range', async () => {
    renderDialog()
    fireEvent.click(screen.getByRole('radio', { name: /Word/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Include synopsis/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Skip empty chapters/ }))
    fireEvent.change(screen.getByLabelText('From chapter'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('To chapter'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(exportBook).toHaveBeenCalled())
    expect(exportBook).toHaveBeenCalledWith('b-1', 'docx', 'Bored to Undeath', {
      includeSynopsis: true,
      skipEmpty: false,
      fromChapter: 2,
      toChapter: 3,
    })
  })

  it('blocks an impossible range before it reaches the server', () => {
    renderDialog(4)
    fireEvent.change(screen.getByLabelText('From chapter'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('To chapter'), { target: { value: '2' } })
    expect(screen.getByRole('alert')).toHaveTextContent('"From" must not be after "To".')
    const download = screen.getByRole('button', { name: 'Download' })
    expect(download).toBeDisabled()
    fireEvent.click(download)
    expect(exportBook).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and says why when the export fails', async () => {
    exportBook.mockRejectedValueOnce({ message: 'from_chapter must be <= to_chapter' })
    const props = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(await screen.findByText('from_chapter must be <= to_chapter')).toBeVisible()
    expect(props.onClose).not.toHaveBeenCalled()
  })
})
