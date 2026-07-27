/**
 * Dashboard — a book-centric overview. The book is the root, so the dashboard
 * leads with your books; sources/characters/scenes are reached inside them.
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { BookMarked, FileText, Film, Plus } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Loading from '@/components/Loading'
import { useBookStore } from '@/lib/books-store'
import { useSourceStore, useSceneStore } from '@/lib/store'
import { formatRelativeTime } from '@/lib/utils'

const BOOK_STATUS_STYLES: Record<string, string> = {
  drafting: 'bg-yellow-100 text-yellow-700',
  revising: 'bg-blue-100 text-blue-700',
  complete: 'bg-green-100 text-green-700',
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
            Your books — sources, characters, canon, and scenes live inside each one.
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
                  <p className="text-sm font-medium text-gray-600">{stat.title}</p>
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
          <Button variant="ghost" size="sm" onClick={() => router.push('/books')}>
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
            {recentBooks.map((book) => (
              <button
                key={book.id}
                onClick={() => router.push(`/books/detail?id=${book.id}`)}
                className="text-left p-4 rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors"
              >
                <div className="flex items-start justify-between mb-2 gap-2">
                  <h3 className="font-semibold text-gray-900 truncate">
                    {book.title}
                  </h3>
                  <span
                    className={`px-2 py-0.5 text-xs font-medium rounded-full shrink-0 ${
                      BOOK_STATUS_STYLES[book.status] || 'bg-gray-100 text-gray-600'
                    }`}
                  >
                    {book.status}
                  </span>
                </div>
                <p className="text-sm text-gray-500">
                  {book.genre || 'No genre'}
                  {book.author ? ` • ${book.author}` : ''}
                </p>
                {book.created_at && (
                  <p className="text-xs text-gray-400 mt-2">
                    {formatRelativeTime(new Date(book.created_at))}
                  </p>
                )}
              </button>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
