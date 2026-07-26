/**
 * Sources Page — upload a file OR paste text into a book. Both run the same
 * reviewed-extraction flow (docs/BRD.md R4.4); a `?book=<id>` param targets an
 * existing book (e.g. one created directly with no source yet).
 */

'use client'

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Plus, FileText, Trash2, Eye } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Modal from '@/components/Modal'
import FileUpload from '@/components/FileUpload'
import Loading from '@/components/Loading'
import { useSourceStore } from '@/lib/store'
import { formatRelativeTime } from '@/lib/utils'

type AddMode = 'upload' | 'paste'

function SourcesContent() {
  const router = useRouter()
  const params = useSearchParams()
  const bookId = params.get('book') || undefined
  const { sources, isLoading, fetchSources, uploadSource, pasteSource, deleteSource } =
    useSourceStore()

  const [modalOpen, setModalOpen] = useState(false)
  const [mode, setMode] = useState<AddMode>('upload')
  const [searchQuery, setSearchQuery] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetchSources()
  }, [fetchSources])

  const resetForm = () => {
    setTitle('')
    setFile(null)
    setPasteText('')
    setError(null)
    setMode('upload')
  }

  const closeModal = () => {
    setModalOpen(false)
    resetForm()
  }

  const handleSubmit = async () => {
    if (!title.trim()) {
      setError('Please give the source a title')
      return
    }
    if (mode === 'upload' && !file) {
      setError('Please choose a file')
      return
    }
    if (mode === 'paste' && !pasteText.trim()) {
      setError('Please paste some text')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const res =
        mode === 'upload'
          ? await uploadSource(file as File, title.trim(), undefined, bookId)
          : await pasteSource(title.trim(), pasteText, bookId)
      closeModal()
      // The source PROPOSES canon; go straight to review + commit — nothing is
      // written until the author approves (docs/BRD.md R4.4).
      router.push(`/sources/review?book=${res.book_id}&run=${res.extraction_run_id}`)
    } catch (err: any) {
      setError(err.message || 'Could not add the source. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (id: string, sourceTitle: string) => {
    if (confirm(`Are you sure you want to delete "${sourceTitle}"?`)) {
      try {
        await deleteSource(id)
        await fetchSources()
      } catch (err: any) {
        alert(err.message || 'Failed to delete source')
      }
    }
  }

  const filteredSources = sources?.filter((m) =>
    m.title.toLowerCase().includes(searchQuery.toLowerCase())
  )

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading sources..." />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Sources</h1>
          <p className="text-gray-600">
            Upload a file or paste text; we extract characters and canon for your
            review.
          </p>
        </div>
        <Button onClick={() => setModalOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Add Source
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <Input
          placeholder="Search sources..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="max-w-md"
        />
      </div>

      {/* Sources Grid */}
      {!filteredSources || filteredSources.length === 0 ? (
        <Card className="text-center py-12">
          <FileText className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            {searchQuery ? 'No sources found' : 'No sources yet'}
          </h3>
          <p className="text-gray-600 mb-6">
            {searchQuery
              ? 'Try adjusting your search query'
              : 'Add your first source — upload a file or paste text — to get started'}
          </p>
          {!searchQuery && (
            <Button onClick={() => setModalOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Add Source
            </Button>
          )}
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredSources.map((source) => (
            <Card key={source.id} hover className="flex flex-col">
              <div className="flex-1">
                <div className="flex items-start justify-between mb-3">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <FileText className="h-6 w-6 text-primary-600" />
                  </div>
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded-full ${
                      source.processing_status === 'completed'
                        ? 'bg-green-100 text-green-700'
                        : source.processing_status === 'processing'
                        ? 'bg-yellow-100 text-yellow-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {source.processing_status}
                  </span>
                </div>

                <h3 className="text-lg font-semibold text-gray-900 mb-2">
                  {source.title}
                </h3>

                <div className="space-y-1 text-sm text-gray-600 mb-4">
                  <p>
                    {source.character_count || 0} characters •{' '}
                    {source.word_count || 0} words
                  </p>
                  <p>Added {formatRelativeTime(new Date(source.created_at))}</p>
                </div>
              </div>

              <div className="flex items-center space-x-2 pt-4 border-t border-gray-200">
                <Button
                  variant="outline"
                  size="sm"
                  fullWidth
                  onClick={() => router.push(`/sources/detail?id=${source.id}`)}
                >
                  <Eye className="h-4 w-4 mr-2" />
                  View
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDelete(source.id, source.title)
                  }}
                >
                  <Trash2 className="h-4 w-4 text-red-500" />
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Add-source Modal */}
      <Modal isOpen={modalOpen} onClose={closeModal} title="Add Source" size="md">
        <div className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{error}</p>
            </div>
          )}

          {/* Mode toggle */}
          <div className="flex rounded-lg border border-gray-200 p-1 bg-gray-50">
            {(['upload', 'paste'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`flex-1 text-sm py-1.5 rounded-md transition-colors ${
                  mode === m
                    ? 'bg-white shadow text-gray-900 font-medium'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {m === 'upload' ? 'Upload file' : 'Paste text'}
              </button>
            ))}
          </div>

          <Input
            label="Source Title"
            placeholder="Enter a title for your source"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />

          {mode === 'upload' ? (
            <FileUpload
              label="Source File"
              accept=".txt,.doc,.docx,.pdf"
              maxSize={10 * 1024 * 1024} // 10MB
              onFileSelect={(f) => setFile(f)}
              helperText="Supported formats: TXT, DOC, DOCX, PDF (max 10MB)"
            />
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Text
              </label>
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder="Paste your notes, a scene, character descriptions…"
                rows={10}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
              <p className="text-xs text-gray-500 mt-1">
                We&apos;ll extract characters and canon for you to review — nothing
                is saved until you approve it.
              </p>
            </div>
          )}

          <div className="flex items-center space-x-3 pt-4">
            <Button variant="outline" fullWidth onClick={closeModal}>
              Cancel
            </Button>
            <Button
              fullWidth
              onClick={handleSubmit}
              isLoading={submitting}
              disabled={
                !title.trim() ||
                (mode === 'upload' ? !file : !pasteText.trim())
              }
            >
              {mode === 'upload' ? 'Upload' : 'Add text'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default function SourcesPage() {
  return (
    <Suspense fallback={<Loading />}>
      <SourcesContent />
    </Suspense>
  )
}
