/**
 * Books Page — list your books and start new ones.
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { BookMarked, Eye, Plus, Trash2 } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import { useBookStore } from '@/lib/books-store'
import { formatRelativeTime } from '@/lib/utils'

const STATUS_STYLES: Record<string, string> = {
  drafting: 'bg-yellow-100 text-yellow-700',
  revising: 'bg-blue-100 text-blue-700',
  complete: 'bg-green-100 text-green-700',
}

export default function BooksPage() {
  const router = useRouter()
  const { books, isLoading, fetchBooks, createBook, deleteBook } = useBookStore()

  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [createData, setCreateData] = useState({
    title: '',
    author: '',
    synopsis: '',
    genre: '',
  })
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    fetchBooks().catch(() => {
      // error state handled by the store
    })
  }, [fetchBooks])

  const closeCreateModal = () => {
    setCreateModalOpen(false)
    setCreateData({ title: '', author: '', synopsis: '', genre: '' })
    setCreateError(null)
  }

  const handleCreate = async () => {
    if (!createData.title.trim()) {
      setCreateError('Please give the book a title')
      return
    }

    setCreateError(null)
    setCreating(true)
    try {
      const { id } = await createBook({
        title: createData.title.trim(),
        ...(createData.author.trim() ? { author: createData.author.trim() } : {}),
        ...(createData.synopsis.trim() ? { synopsis: createData.synopsis.trim() } : {}),
        ...(createData.genre.trim() ? { genre: createData.genre.trim() } : {}),
      })
      closeCreateModal()
      router.push(`/books/detail?id=${id}`)
    } catch (err: any) {
      setCreateError(err.message || 'Failed to create book')
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: string, title: string) => {
    if (confirm(`Are you sure you want to delete "${title}"? This removes all its chapters and scenes.`)) {
      try {
        await deleteBook(id)
      } catch (err: any) {
        alert(err.message || 'Failed to delete book')
      }
    }
  }

  if (isLoading && books.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading books..." />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Books</h1>
          <p className="text-gray-600">
            Plan, draft, and revise full-length books chapter by chapter
          </p>
        </div>
        <Button onClick={() => setCreateModalOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          New Book
        </Button>
      </div>

      {/* Books Grid */}
      {books.length === 0 ? (
        <Card className="text-center py-12">
          <BookMarked className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No books yet</h3>
          <p className="text-gray-600 mb-6">
            Create your first book to start outlining and drafting
          </p>
          <Button onClick={() => setCreateModalOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            New Book
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {books.map((book) => (
            <Card key={book.id} hover className="flex flex-col">
              <div
                className="flex-1"
                onClick={() => router.push(`/books/detail?id=${book.id}`)}
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <BookMarked className="h-6 w-6 text-primary-600" />
                  </div>
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded-full ${
                      STATUS_STYLES[book.status] || 'bg-gray-100 text-gray-700'
                    }`}
                  >
                    {book.status}
                  </span>
                </div>

                <h3 className="text-lg font-semibold text-gray-900 mb-2">
                  {book.title}
                </h3>

                <div className="space-y-1 text-sm text-gray-600 mb-4">
                  <p>
                    {book.genre || 'No genre'}
                    {book.author ? ` • by ${book.author}` : ''}
                  </p>
                  {book.created_at && (
                    <p>Created {formatRelativeTime(new Date(book.created_at))}</p>
                  )}
                </div>
              </div>

              <div className="flex items-center space-x-2 pt-4 border-t border-gray-200">
                <Button
                  variant="outline"
                  size="sm"
                  fullWidth
                  onClick={() => router.push(`/books/detail?id=${book.id}`)}
                >
                  <Eye className="h-4 w-4 mr-2" />
                  Open
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDelete(book.id, book.title)
                  }}
                >
                  <Trash2 className="h-4 w-4 text-red-500" />
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <Modal
        isOpen={createModalOpen}
        onClose={closeCreateModal}
        title="New Book"
        size="md"
      >
        <div className="space-y-4">
          {createError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{createError}</p>
            </div>
          )}

          <Input
            label="Title"
            placeholder="The working title of your book"
            value={createData.title}
            onChange={(e) => setCreateData({ ...createData, title: e.target.value })}
            required
          />

          <Input
            label="Author"
            placeholder="Author name (optional)"
            value={createData.author}
            onChange={(e) => setCreateData({ ...createData, author: e.target.value })}
          />

          <Input
            label="Genre"
            placeholder="e.g., Fantasy, Thriller (optional)"
            value={createData.genre}
            onChange={(e) => setCreateData({ ...createData, genre: e.target.value })}
          />

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Synopsis
            </label>
            <textarea
              value={createData.synopsis}
              onChange={(e) =>
                setCreateData({ ...createData, synopsis: e.target.value })
              }
              placeholder="A short premise — required later for outline generation"
              rows={4}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          <div className="flex items-center space-x-3 pt-4">
            <Button variant="outline" fullWidth onClick={closeCreateModal}>
              Cancel
            </Button>
            <Button
              fullWidth
              onClick={handleCreate}
              isLoading={creating}
              disabled={!createData.title.trim()}
            >
              Create Book
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
