/**
 * Manuscript Detail Page
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { ArrowLeft, Users, FileText, Wand2 } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Loading from '@/components/Loading'
import apiClient from '@/lib/api-client'
import { Manuscript, Character } from '@/lib/types'
import { formatRelativeTime } from '@/lib/utils'

export default function ManuscriptDetailPage() {
  const router = useRouter()
  const params = useParams()
  const manuscriptId = params.id as string

  const [manuscript, setManuscript] = useState<Manuscript | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const loadManuscript = async () => {
      try {
        setIsLoading(true)
        const [manuscriptData, charactersData] = await Promise.all([
          apiClient.getManuscript(manuscriptId),
          apiClient.getManuscriptCharacters(manuscriptId),
        ])
        setManuscript(manuscriptData)
        setCharacters(charactersData)
      } catch (err: any) {
        setError(err.message || 'Failed to load manuscript')
      } finally {
        setIsLoading(false)
      }
    }

    loadManuscript()
  }, [manuscriptId])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading manuscript..." />
      </div>
    )
  }

  if (error || !manuscript) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Card className="text-center py-12">
          <FileText className="h-16 w-16 text-red-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            Error Loading Manuscript
          </h3>
          <p className="text-gray-600 mb-6">{error}</p>
          <Button onClick={() => router.push('/manuscripts')}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Manuscripts
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push('/manuscripts')}
          className="mb-4"
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Back to Manuscripts
        </Button>

        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">
              {manuscript.title}
            </h1>
            <p className="text-gray-600">
              Uploaded {formatRelativeTime(new Date(manuscript.created_at))}
            </p>
          </div>
          <span
            className={`px-3 py-1 text-sm font-medium rounded-full ${
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
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <Card>
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <FileText className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Words</p>
              <p className="text-2xl font-bold text-gray-900">
                {manuscript.word_count?.toLocaleString() || 0}
              </p>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-green-100 rounded-lg">
              <Users className="h-5 w-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Characters</p>
              <p className="text-2xl font-bold text-gray-900">
                {manuscript.character_count || 0}
              </p>
            </div>
          </div>
        </Card>

        <Card>
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-purple-100 rounded-lg">
              <Wand2 className="h-5 w-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-600">Generate</p>
              <Button
                size="sm"
                onClick={() =>
                  router.push(`/generate?manuscript=${manuscript.id}`)
                }
                disabled={manuscript.processing_status !== 'completed'}
              >
                New Scene
              </Button>
            </div>
          </div>
        </Card>
      </div>

      {/* Characters */}
      <Card>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">
          Characters
        </h2>

        {manuscript.processing_status === 'processing' ? (
          <div className="text-center py-8">
            <Loading text="Processing characters..." />
            <p className="text-sm text-gray-600 mt-4">
              This may take a few minutes depending on the manuscript size
            </p>
          </div>
        ) : !characters || characters.length === 0 ? (
          <div className="text-center py-8">
            <Users className="h-12 w-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-600">
              No characters found in this manuscript
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {characters.map((character) => (
              <div
                key={character.id}
                className="p-4 rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <h3 className="font-semibold text-gray-900">
                    {character.name}
                  </h3>
                  <span className="px-2 py-1 text-xs font-medium bg-gray-100 text-gray-700 rounded">
                    {character.total_chunks || 0} chunks
                  </span>
                </div>

                {character.description && (
                  <p className="text-sm text-gray-600 mb-3 line-clamp-3">
                    {character.description}
                  </p>
                )}

                {character.traits && character.traits.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {character.traits.slice(0, 3).map((trait, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 text-xs bg-primary-100 text-primary-700 rounded"
                      >
                        {trait}
                      </span>
                    ))}
                    {character.traits.length > 3 && (
                      <span className="px-2 py-0.5 text-xs text-gray-500">
                        +{character.traits.length - 3} more
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
