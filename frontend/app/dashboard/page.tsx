/**
 * Dashboard — a book-centric overview. The book is the root, so the dashboard
 * leads with your books; sources/characters/scenes are reached inside them.
 * Each card shows the book's progress (words, scenes drafted out of planned)
 * and a "Resume" link to the last-edited scene (SPEC-27, B12).
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { BookMarked, FileText, Film, PenLine, Plus } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Loading from '@/components/Loading'
import { useBookStore } from '@/lib/books-store'
import { useSourceStore, useSceneStore } from '@/lib/store'
import { formatRelativeTime } from '@/lib/utils'
import type { BookSummary } from '@/lib/types'

const BOOK_STATUS_STYLES: Record<string, string> = {
  drafting: 'bg-yellow-100 text-yellow-700',
  revising: 'bg-blue-100 text-blue-700',
  complete: 'bg-green-100 text-green-700',
}

const plural = (n: number, word: string) =>
  `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`

/** "1,234 words · 3/8 scenes drafted · 2 chapters", from the list's progress
 *  fields; null when the API predates them. */
function bookProgressLine(book: BookSummary): string | null {
  if (book.word_count == null) return null
  const parts = [plural(book.word_count, 'word')]
  const drafted = book.scenes_drafted ?? 0
  const planned = book.scenes_planned ?? 0
  parts.push(
    planned > 0
      ? `${drafted}/${planned} planned scenes drafted`
      : `${plural(drafted, 'scene')} drafted`
  )
  if (book.chapter_count != null)
    parts.push(
      `${book.chapters_drafted ?? 0}/${plural(book.chapter_count, 'chapter')} drafted`
    )
  return parts.join(' · ')
}

export default function DashboardPage() {
  const router = useRouter()
  const { books, isLoading: booksLoading, fetchBooks } = useBookStore()
  const { sources, fetchSources } = useSourceStore()
  const { scenes, fetchScenes } = useSceneStore()

  useEffect(() => {
    fetchBooks().catch(() => {})
    fetchSources().catch(() => {})
    fetchScenes().catch(() => {})
  }, [fetchBooks, fetchSources, fetchScenes])

  if (booksLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading dashboard..." />
      </div>
    )
  }

  const statCards = [
    {
      title: 'Books',
      value: books.length,
      icon: BookMarked,
      color: 'text-primary-600',
      bgColor: 'bg-primary-50',
    },
    {
      title: 'Sources',
      value: sources?.length ?? 0,
      icon: FileText,
      color: 'text-blue-600',
      bgColor: 'bg-blue-50',
    },
    {
      title: 'Scenes',
      value: scenes?.length ?? 0,
      icon: Film,
      color: 'text-purple-600',
      bgColor: 'bg-purple-50',
    },
  ]

  const recentBooks = [...books].slice(0, 6)

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Dashboard</h1>
          <p className="text-gray-600">
            Your books — sources, characters, canon, and scenes live inside each
            one.
          </p>
        </div>
        <Button onClick={() => router.push('/books')}>
          <BookMarked className="h-4 w-4 mr-2" />
          My Books
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
        {statCards.map((stat) => {
          const Icon = stat.icon
          return (
            <Card key={stat.title} padding="md">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-600">
                    {stat.title}
                  </p>
                  <p className="text-2xl font-bold text-gray-900 mt-1">
                    {stat.value}
                  </p>
                </div>
                <div className={`p-3 rounded-lg ${stat.bgColor}`}>
                  <Icon className={`h-6 w-6 ${stat.color}`} />
                </div>
              </div>
            </Card>
          )
        })}
      </div>

      {/* Recent Books */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">Your Books</h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/books')}
          >
            View All
          </Button>
        </div>

        {recentBooks.length === 0 ? (
          <div className="text-center py-12">
            <BookMarked className="h-12 w-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-600 mb-4">No books yet</p>
            <Button onClick={() => router.push('/books')}>
              <Plus className="h-4 w-4 mr-2" />
              Create your first book
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {recentBooks.map((book) => {
              const progress = bookProgressLine(book)
              const resume = book.last_edited_scene
              return (
                <div
                  key={book.id}
                  className="flex flex-col rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors"
                  data-testid="dashboard-book"
                >
                  <button
                    onClick={() => router.push(`/books/detail?id=${book.id}`)}
                    className="flex-1 text-left p-4"
                  >
                    <div className="flex items-start justify-between mb-2 gap-2">
                      <h3 className="font-semibold text-gray-900 truncate">
                        {book.title}
                      </h3>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded-full shrink-0 ${
                          BOOK_STATUS_STYLES[book.status] ||
                          'bg-gray-100 text-gray-600'
                        }`}
                      >
                        {book.status}
                      </span>
                    </div>
                    <p className="text-sm text-gray-500">
                      {book.genre || 'No genre'}
                      {book.author ? ` • ${book.author}` : ''}
                    </p>
                    {progress && (
                      <p className="text-xs text-gray-600 mt-2">{progress}</p>
                    )}
                    {book.created_at && (
                      <p className="text-xs text-gray-400 mt-2">
                        {formatRelativeTime(new Date(book.created_at))}
                      </p>
                    )}
                  </button>
                  {resume && (
                    <button
                      onClick={() =>
                        router.push(
                          `/books/editor?scene=${resume.id}&book=${book.id}`
                        )
                      }
                      className="mx-4 mb-3 inline-flex items-center gap-1 self-start text-sm font-medium text-primary-700 hover:underline"
                    >
                      <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
                      Resume: {resume.title || 'Untitled scene'}
                      {resume.chapter_title ? (
                        <span className="text-gray-500 font-normal">
                          {' '}
                          ({resume.chapter_title})
                        </span>
                      ) : null}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
