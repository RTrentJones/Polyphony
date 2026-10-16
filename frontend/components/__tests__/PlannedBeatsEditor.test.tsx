import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PlannedBeatsEditor from '@/components/book/PlannedBeatsEditor'
import type { ChapterDetail, Character, PlannedBeat } from '@/lib/types'
import { bookValue, renderWithBook } from './bookContext'

const { updateChapter } = vi.hoisted(() => ({ updateChapter: vi.fn() }))
vi.mock('@/lib/books-store', () => ({ useBookStore: () => ({ updateChapter }) }))

const cast: Character[] = [
  { id: 'c-milo', name: 'Milo' },
  { id: 'c-zara', name: 'Zara' },
  { id: 'c-edric', name: 'Edric' },
]
const beat = (n: number, over: Partial<PlannedBeat> = {}): PlannedBeat => ({
  node_id: `n-${n}`, title: `Beat ${n}`, summary: `s${n}`, character_ids: [],
  secondary_names: [], setting: '', tone: '', cast_ids: ['c-milo', 'c-zara'], ...over,
})
const chapter = (over: Partial<ChapterDetail> = {}): ChapterDetail => ({
  id: 'ch-1', book_id: 'book-1', position: 0, number: 1, title: 'Intake', summary: 'Milo arrives',
  pov_character_id: 'c-milo', character_ids: ['c-milo', 'c-zara'],
  planned_beats: [beat(1), beat(2, { character_ids: ['c-edric'], cast_ids: ['c-milo', 'c-edric'] })],
  scenes: [], written: {}, ...over,
})

function open(ch = chapter()) {
  const props = { chapter: ch, onSaved: vi.fn(), onCancel: vi.fn() }
  const value = renderWithBook(
    <PlannedBeatsEditor {...props} />,
    bookValue({ characters: cast, refreshCharacters: vi.fn().mockResolvedValue(cast) })
  )
  return { ...props, value }
}
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save chapter 1' }))
const sent = async () => {
  await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1))
  return updateChapter.mock.calls[0][1]
}
const chip = (n: number, name: string) =>
  within(screen.getByRole('group', { name: `Cast of beat ${n} of chapter 1` })).getByRole(
    'button', { name: new RegExp(`^${name}`) }
  )

describe('PlannedBeatsEditor (SPEC-35)', () => {
  beforeEach(() => {
    updateChapter.mockReset().mockResolvedValue(undefined)
  })

  it('refreshes the cast on entering edit mode, and sends nothing when nothing changed', async () => {
    const { value, onCancel } = open()
    expect(value.refreshCharacters).toHaveBeenCalled()
    save()
    expect(onCancel).toHaveBeenCalled()
    expect(updateChapter).not.toHaveBeenCalled()
  })

  it('B-2: a rename sends {title} with no planned_beats', async () => {
    const { onSaved } = open()
    fireEvent.change(screen.getByLabelText('Title of chapter 1'), { target: { value: 'Arrival' } })
    save()
    expect(await sent()).toEqual({ title: 'Arrival' })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it("B-2: editing beat 2's summary sends beat 1 with its stored character_ids ([])", async () => {
    open()
    fireEvent.change(screen.getByLabelText('Beat 2 of chapter 1, summary'), { target: { value: 'New' } })
    save()
    const body = await sent()
    expect(Object.keys(body)).toEqual(['planned_beats'])
    expect(body.planned_beats[0]).toEqual({
      node_id: 'n-1', title: 'Beat 1', summary: 's1', character_ids: [],
      secondary_names: [], setting: '', tone: '',
    })
    expect(body.planned_beats[1]).toMatchObject({ summary: 'New', character_ids: ['c-edric'] })
  })

  it('an untouched beat drops a character since moved to the Trash', async () => {
    open(chapter({ planned_beats: [beat(1, { character_ids: ['c-edric', 'c-gone'] })] }))
    fireEvent.change(screen.getByLabelText('Beat 1 of chapter 1, summary'), { target: { value: 'New' } })
    save()
    expect((await sent()).planned_beats[0].character_ids).toEqual(['c-edric'])
  })

  it("toggling beat 1's chips sends the shown chips; the POV chip cannot be removed", async () => {
    open()
    expect(chip(1, 'Milo')).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(chip(1, 'Milo'))
    fireEvent.click(chip(1, 'Zara'))
    fireEvent.click(chip(1, 'Edric'))
    save()
    expect((await sent()).planned_beats[0].character_ids).toEqual(['c-milo', 'c-edric'])
  })

  it('marks the beat a strict 422 names, with the server message', async () => {
    updateChapter.mockRejectedValue({
      message: 'Not in cast (c-edric)', status: 422,
      detail: { message: 'Not in this book.', unknown: ['c-edric'] },
    })
    open()
    fireEvent.change(screen.getByLabelText('Beat 1 of chapter 1, tone'), { target: { value: 'wry' } })
    save()
    const beat2 = await screen.findByRole('group', { name: 'Beat 2 of chapter 1' })
    expect(within(beat2).getByRole('alert')).toHaveTextContent('Not in this book.')
    const beat1 = screen.getByRole('group', { name: 'Beat 1 of chapter 1' })
    expect(within(beat1).queryByRole('alert')).toBeNull()
  })

  it("Delete and Add edit the list; shows a Pydantic 422's first msg", async () => {
    updateChapter.mockRejectedValue({
      message: 'String should have at most 500 characters', status: 422,
      detail: [{ loc: ['body'], msg: 'String should have at most 500 characters' }],
    })
    open(chapter({ planned_beats: [beat(1), beat(2), beat(3)] }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete beat 1 of chapter 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add beat to chapter 1' }))
    fireEvent.change(screen.getByLabelText('Beat 3 of chapter 1, title'), { target: { value: 'Coda' } })
    save()
    expect(await screen.findByRole('alert')).toHaveTextContent('String should have at most 500 characters')
    const beats = (await sent()).planned_beats
    expect(beats.map((b: PlannedBeat) => b.node_id)).toEqual(['n-2', 'n-3', ''])
    expect(beats[2]).toMatchObject({ title: 'Coda', character_ids: [] })
  })

  it('a blank chapter title is refused before saving', async () => {
    open()
    fireEvent.change(screen.getByLabelText('Title of chapter 1'), { target: { value: '  ' } })
    save()
    expect(await screen.findByRole('alert')).toHaveTextContent('The chapter needs a title')
    expect(updateChapter).not.toHaveBeenCalled()
  })

  it('a blank beat title is refused before saving, so no beat is silently dropped', async () => {
    open()
    fireEvent.change(screen.getByLabelText('Beat 2 of chapter 1, title'), { target: { value: '   ' } })
    save()
    expect(await screen.findByRole('alert')).toHaveTextContent('Every beat needs a title')
    expect(updateChapter).not.toHaveBeenCalled()
  })

  it('a written beat links to its scene and says editing it does not change the scene', () => {
    open(chapter({
      written: { 'n-2': 's-9' },
      scenes: [{ id: 's-9', position: 0, status: 'completed', created_at: null, title: 'The file' }],
    }))
    const beat2 = screen.getByRole('group', { name: 'Beat 2 of chapter 1' })
    expect(within(beat2).getByRole('link', { name: /The file/ })).toHaveAttribute(
      'href', '/books/editor?scene=s-9&book=book-1'
    )
    expect(beat2).toHaveTextContent('Editing this beat does not change its scene.')
    expect(screen.getByText('1 of 2 planned scenes written')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Beat 1 of chapter 1' })).queryByRole('link')).toBeNull()
  })
})
