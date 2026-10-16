import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChaptersSection from '@/components/book/ChaptersSection'
import RowActionsMenu from '@/components/book/RowActionsMenu'
import type { BookChapter } from '@/lib/types'
import { displayChapterTitle } from '@/lib/utils'
import { bookValue, renderWithBook } from './bookContext'

const { generatePlannedScenes } = vi.hoisted(() => ({ generatePlannedScenes: vi.fn() }))

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/lib/canon-store', () => ({ canonApi: { listCharacters: vi.fn() } }))
vi.mock('@/lib/books-store', () => ({
  useBookStore: () => ({
    chapterDetails: {},
    createChapter: vi.fn(),
    moveChapter: vi.fn(),
    moveScene: vi.fn(),
    generatePlannedScenes,
  }),
}))
vi.mock('@/components/VersionHistory', () => ({
  default: ({ label }: { label: string }) => <div role="dialog">History — {label}</div>,
}))
vi.mock('@/components/book/DeleteChapterModal', () => ({
  default: ({ chapter }: { chapter: BookChapter }) => (
    <div role="dialog">Delete {chapter.title}?</div>
  ),
}))

describe('displayChapterTitle (B4/B9)', () => {
  it.each([
    ['Chapter 2: Milo Voss', 'Milo Voss'],
    ['Chapter 12. The Audit', 'The Audit'],
    ['chapter 3 - Debts', 'Debts'],
    ['Chapter 4 – Interest', 'Interest'],
    ['Onboarding', 'Onboarding'],
    ['Chapter 7', 'Chapter 7'],
    ['Chapter 1:', 'Chapter 1:'],
    ['The Chapter 2: problem', 'The Chapter 2: problem'],
    ['', ''],
  ])('%j reads %j', (raw, shown) => {
    expect(displayChapterTitle(raw)).toBe(shown)
  })
})

const promoted: BookChapter = {
  id: 'ch-3',
  book_id: 'book-1',
  position: 2,
  number: 3,
  title: 'Chapter 2: Milo Voss, who audits the afterlife and finds it insolvent',
  // After the SPEC-23 migration: the beats are data, the summary is just prose.
  summary: 'Milo arrives',
  planned_beats: [
    { node_id: 'n-1', title: 'Badge photo', summary: '', character_ids: [], secondary_names: [], setting: '', tone: '', cast_ids: [] },
    { node_id: 'n-2', title: 'Lunch', summary: '', character_ids: [], secondary_names: [], setting: '', tone: '', cast_ids: [] },
  ],
}
const label = `chapter 3, ${displayChapterTitle(promoted.title)}`
const plain: BookChapter = { id: 'ch-1', book_id: 'book-1', position: 0, number: 1, title: 'Onboarding' }

function renderSection(chapters = [plain, promoted]) {
  const addToast = vi.fn()
  renderWithBook(
    <ChaptersSection onSceneGenerated={vi.fn()} addToast={addToast} />,
    bookValue({ chapters })
  )
  return { addToast }
}

describe('ChaptersSection at phone width (B4)', () => {
  beforeEach(() => generatePlannedScenes.mockReset())

  it('shows the whole title, numbered by position, without a stale "Chapter N:"', () => {
    renderSection()
    const titles = screen.getAllByTestId('chapter-title')
    expect(titles[1]).toHaveTextContent(
      '3. Milo Voss, who audits the afterlife and finds it insolvent'
    )
    // Wraps to two lines instead of truncating to a few characters.
    expect(titles[1].className).toContain('line-clamp-2')
    expect(titles[1].className).not.toMatch(/\btruncate\b/)
  })

  it('stacks title over actions below sm, and moves secondary actions into the menu', () => {
    renderSection()
    const row = screen.getAllByTestId('chapter-row')[1]
    const header = row.firstElementChild as HTMLElement
    expect(header.className).toMatch(/\bflex-col\b/)
    expect(header.className).toContain('sm:flex-row')
    const inRow = within(row).getByRole('button', { name: `History of ${label}` })
    expect(inRow.parentElement?.className).toMatch(/\bhidden\b.*\bsm:flex\b/)
    const trigger = within(row).getByRole('button', {
      name: `More actions for ${label}`,
    })
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
    expect(trigger.parentElement?.className).toContain('sm:hidden')
    // Reorder arrows stay in the row at every width.
    expect(
      within(row).getByRole('button', { name: `Move ${label} up` }).parentElement
        ?.className
    ).not.toContain('hidden')
  })

  it('offers planned scenes, history and delete from the menu', async () => {
    generatePlannedScenes.mockResolvedValue({ queued: 2, skipped: 0, total: 2 })
    const { addToast } = renderSection()
    const row = screen.getAllByTestId('chapter-row')[1]
    fireEvent.click(within(row).getByRole('button', { name: /More actions for/ }))
    const menu = screen.getByRole('menu')
    expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
      'Generate all planned scenes',
      'History',
      'Delete chapter',
    ])
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Generate all planned scenes' }))
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Writing 2 planned scenes in order.', 'success')
    )
    expect(generatePlannedScenes).toHaveBeenCalledWith('ch-3')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: /More actions for/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete chapter' }))
    expect(screen.getByRole('dialog')).toHaveTextContent(`Delete ${promoted.title}?`)
  })

  it('leaves planned scenes out of the menu for a chapter without planned beats', () => {
    renderSection()
    const row = screen.getAllByTestId('chapter-row')[0]
    fireEvent.click(within(row).getByRole('button', { name: /More actions for/ }))
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
      'History',
      'Delete chapter',
    ])
  })
})

describe('RowActionsMenu keyboard (B4)', () => {
  function renderMenu() {
    const history = vi.fn()
    const remove = vi.fn()
    render(
      <RowActionsMenu
        label="More actions for Onboarding"
        actions={[
          { key: 'h', label: 'History', onSelect: history },
          { key: 'x', label: 'Disabled', onSelect: vi.fn(), disabled: true },
          { key: 'd', label: 'Delete chapter', onSelect: remove, danger: true },
        ]}
      />
    )
    return { history, remove, trigger: screen.getByRole('button', { name: /More actions/ }) }
  }

  it('opens from the keyboard, focuses the first item and skips disabled ones', () => {
    const { trigger } = renderMenu()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const items = screen.getAllByRole('menuitem')
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(items[2]).toHaveFocus()
    fireEvent.keyDown(items[2], { key: 'ArrowDown' })
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(items[0], { key: 'End' })
    expect(items[2]).toHaveFocus()
    fireEvent.keyDown(items[2], { key: 'Home' })
    expect(items[0]).toHaveFocus()
  })

  it('closes on Escape and returns focus to the trigger', () => {
    const { trigger } = renderMenu()
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getAllByRole('menuitem')[0], { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('runs the chosen item and closes', () => {
    const { trigger, remove } = renderMenu()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete chapter' }))
    expect(remove).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes on a click outside', () => {
    const { trigger } = renderMenu()
    fireEvent.click(trigger)
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
