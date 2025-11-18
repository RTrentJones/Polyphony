/**
 * Manuscripts Page
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Plus, FileText, Search, Trash2, Eye } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Modal from '@/components/Modal'
import FileUpload from '@/components/FileUpload'
import Loading from '@/components/Loading'
import { useManuscriptStore } from '@/lib/store'
import { formatRelativeTime } from '@/lib/utils'

export default function ManuscriptsPage() {
  const router = useRouter()
  const {
    manuscripts,
    isLoading,
    fetchManuscripts,
    uploadManuscript,
    deleteManuscript,
  } = useManuscriptStore()

  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [uploadData, setUploadData] = useState({
    file: null as File | null,
    title: '',
  })
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    fetchManuscripts()
  }, [fetchManuscripts])

  const handleUploadSubmit = async () => {
    if (!uploadData.file || !uploadData.title) {
      setUploadError('Please provide both a file and a title')
      return
    }

    setUploadError(null)
    setUploading(true)

    try {
      await uploadManuscript(uploadData.file, uploadData.title)
      setUploadModalOpen(false)
      setUploadData({ file: null, title: '' })
      await fetchManuscripts()
    } catch (err: any) {
      setUploadError(err.message || 'Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id: string, title: string) => {
    if (confirm(`Are you sure you want to delete "${title}"?`)) {
      try {
        await deleteManuscript(id)
        await fetchManuscripts()
      } catch (err: any) {
        alert(err.message || 'Failed to delete manuscript')
      }
    }
  }

  const filteredManuscripts = manuscripts?.filter((m) =>
    m.title.toLowerCase().includes(searchQuery.toLowerCase())
  )

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading manuscripts..." />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Manuscripts</h1>
          <p className="text-gray-600">
            Manage your creative writing projects
          </p>
        </div>
        <Button onClick={() => setUploadModalOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Upload Manuscript
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <Input
          placeholder="Search manuscripts..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="max-w-md"
        />
      </div>

      {/* Manuscripts Grid */}
      {!filteredManuscripts || filteredManuscripts.length === 0 ? (
        <Card className="text-center py-12">
          <FileText className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            {searchQuery ? 'No manuscripts found' : 'No manuscripts yet'}
          </h3>
          <p className="text-gray-600 mb-6">
            {searchQuery
              ? 'Try adjusting your search query'
              : 'Upload your first manuscript to get started with character analysis'}
          </p>
          {!searchQuery && (
            <Button onClick={() => setUploadModalOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Upload Manuscript
            </Button>
          )}
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredManuscripts.map((manuscript) => (
            <Card
              key={manuscript.id}
              hover
              className="flex flex-col"
            >
              <div className="flex-1">
                <div className="flex items-start justify-between mb-3">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <FileText className="h-6 w-6 text-primary-600" />
                  </div>
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded-full ${
                      manuscript.processing_status === 'completed'
                        ? 'bg-green-100 text-green-700'
                        : manuscript.processing_status === 'processing'
                        ? 'bg-yellow-100 text-yellow-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {manuscript.processing_status}
                  </span>
                </div>

                <h3 className="text-lg font-semibold text-gray-900 mb-2">
                  {manuscript.title}
                </h3>

                <div className="space-y-1 text-sm text-gray-600 mb-4">
                  <p>
                    {manuscript.character_count || 0} characters •{' '}
                    {manuscript.word_count || 0} words
                  </p>
                  <p>
                    Uploaded {formatRelativeTime(new Date(manuscript.created_at))}
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-2 pt-4 border-t border-gray-200">
                <Button
                  variant="outline"
                  size="sm"
                  fullWidth
                  onClick={() => router.push(`/manuscripts/${manuscript.id}`)}
                >
                  <Eye className="h-4 w-4 mr-2" />
                  View
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation()
                    handleDelete(manuscript.id, manuscript.title)
                  }}
                >
                  <Trash2 className="h-4 w-4 text-red-500" />
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Upload Modal */}
      <Modal
        isOpen={uploadModalOpen}
        onClose={() => {
          setUploadModalOpen(false)
          setUploadData({ file: null, title: '' })
          setUploadError(null)
        }}
        title="Upload Manuscript"
        size="md"
      >
        <div className="space-y-4">
          {uploadError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{uploadError}</p>
            </div>
          )}

          <Input
            label="Manuscript Title"
            placeholder="Enter a title for your manuscript"
            value={uploadData.title}
            onChange={(e) =>
              setUploadData({ ...uploadData, title: e.target.value })
            }
            required
          />

          <FileUpload
            label="Manuscript File"
            accept=".txt,.doc,.docx,.pdf"
            maxSize={10 * 1024 * 1024} // 10MB
            onFileSelect={(file) => setUploadData({ ...uploadData, file })}
            helperText="Supported formats: TXT, DOC, DOCX, PDF (max 10MB)"
          />

          <div className="flex items-center space-x-3 pt-4">
            <Button
              variant="outline"
              fullWidth
              onClick={() => {
                setUploadModalOpen(false)
                setUploadData({ file: null, title: '' })
                setUploadError(null)
              }}
            >
              Cancel
            </Button>
            <Button
              fullWidth
              onClick={handleUploadSubmit}
              isLoading={uploading}
              disabled={!uploadData.file || !uploadData.title}
            >
              Upload
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
