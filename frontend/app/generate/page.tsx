/**
 * Scene Generation Page
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Wand2, Users, FileText, Settings } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import { useSourceStore, useSceneStore } from '@/lib/store'
import { SceneRequest } from '@/lib/types'
import apiClient from '@/lib/api-client'

export default function GeneratePage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const preselectedSourceId = searchParams.get('source')

  const { sources, isLoading: sourcesLoading, fetchSources } = useSourceStore()
  const { generateScene } = useSceneStore()

  const [selectedSourceId, setSelectedSourceId] = useState<string>(
    preselectedSourceId || ''
  )
  const [characters, setCharacters] = useState<any[]>([])
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([])
  const [loadingCharacters, setLoadingCharacters] = useState(false)

  const [formData, setFormData] = useState({
    sceneDescription: '',
    setting: '',
    emotionalTone: 'neutral',
    targetWordCount: 500,
  })

  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchSources()
  }, [fetchSources])

  useEffect(() => {
    const loadCharacters = async () => {
      if (!selectedSourceId) {
        setCharacters([])
        setSelectedCharacters([])
        return
      }

      setLoadingCharacters(true)
      try {
        const chars = await apiClient.getSourceCharacters(selectedSourceId)
        setCharacters(chars)
        setSelectedCharacters([])
      } catch (err: any) {
        setError(err.message || 'Failed to load characters')
      } finally {
        setLoadingCharacters(false)
      }
    }

    loadCharacters()
  }, [selectedSourceId])

  const handleCharacterToggle = (characterName: string) => {
    setSelectedCharacters((prev) =>
      prev.includes(characterName)
        ? prev.filter((c) => c !== characterName)
        : [...prev, characterName]
    )
  }

  const handleGenerate = async () => {
    setError(null)

    if (!selectedSourceId) {
      setError('Please select a source')
      return
    }

    if (selectedCharacters.length === 0) {
      setError('Please select at least one character')
      return
    }

    if (!formData.sceneDescription || !formData.setting) {
      setError('Please provide scene description and setting')
      return
    }

    setGenerating(true)

    try {
      const sceneRequest: SceneRequest = {
        source_id: selectedSourceId,
        characters: selectedCharacters,
        scene_description: formData.sceneDescription,
        setting: formData.setting,
        emotional_tone: formData.emotionalTone,
        target_word_count: formData.targetWordCount,
      }

      const result = await generateScene(sceneRequest)

      // Redirect to scene view (if we had one) or back to dashboard
      router.push('/dashboard')
    } catch (err: any) {
      setError(err.message || 'Failed to generate scene')
    } finally {
      setGenerating(false)
    }
  }

  if (sourcesLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading sources..." />
      </div>
    )
  }

  const completedSources = sources?.filter(
    (m) => m.processing_status === 'completed'
  )

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          Generate Scene
        </h1>
        <p className="text-gray-600">
          Create character-driven dialogue using AI-powered generation
        </p>
      </div>

      {error && (
        <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-sm text-red-600">{error}</p>
        </div>
      )}

      {/* No Sources */}
      {!completedSources || completedSources.length === 0 ? (
        <Card className="text-center py-12">
          <FileText className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            No Processed Sources
          </h3>
          <p className="text-gray-600 mb-6">
            You need to upload and process a source before generating scenes
          </p>
          <Button onClick={() => router.push('/sources')}>
            Upload Source
          </Button>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Step 1: Select Source */}
          <Card>
            <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
              <FileText className="h-5 w-5 mr-2 text-primary-600" />
              1. Select Source
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {completedSources.map((source) => (
                <div
                  key={source.id}
                  onClick={() => setSelectedSourceId(source.id)}
                  className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${
                    selectedSourceId === source.id
                      ? 'border-primary-500 bg-primary-50'
                      : 'border-gray-200 hover:border-primary-300'
                  }`}
                >
                  <h3 className="font-medium text-gray-900">
                    {source.title}
                  </h3>
                  <p className="text-sm text-gray-600 mt-1">
                    {source.character_count || 0} characters
                  </p>
                </div>
              ))}
            </div>
          </Card>

          {/* Step 2: Select Characters */}
          {selectedSourceId && (
            <Card>
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <Users className="h-5 w-5 mr-2 text-primary-600" />
                2. Select Characters
              </h2>

              {loadingCharacters ? (
                <Loading text="Loading characters..." />
              ) : !characters || characters.length === 0 ? (
                <p className="text-gray-600">
                  No characters found in this source
                </p>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {characters.map((character) => (
                    <div
                      key={character.id}
                      onClick={() => handleCharacterToggle(character.name)}
                      className={`p-3 rounded-lg border-2 cursor-pointer transition-all ${
                        selectedCharacters.includes(character.name)
                          ? 'border-primary-500 bg-primary-50'
                          : 'border-gray-200 hover:border-primary-300'
                      }`}
                    >
                      <p className="font-medium text-gray-900 text-sm">
                        {character.name}
                      </p>
                      {character.traits && character.traits.length > 0 && (
                        <p className="text-xs text-gray-500 mt-1">
                          {character.traits[0]}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* Step 3: Scene Details */}
          {selectedCharacters.length > 0 && (
            <Card>
              <h2 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <Settings className="h-5 w-5 mr-2 text-primary-600" />
                3. Scene Details
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Scene Description
                  </label>
                  <textarea
                    value={formData.sceneDescription}
                    onChange={(e) =>
                      setFormData({ ...formData, sceneDescription: e.target.value })
                    }
                    placeholder="Describe what happens in this scene..."
                    rows={4}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>

                <Input
                  label="Setting"
                  placeholder="e.g., A dimly lit tavern at midnight"
                  value={formData.setting}
                  onChange={(e) =>
                    setFormData({ ...formData, setting: e.target.value })
                  }
                />

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Emotional Tone
                  </label>
                  <select
                    value={formData.emotionalTone}
                    onChange={(e) =>
                      setFormData({ ...formData, emotionalTone: e.target.value })
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  >
                    <option value="neutral">Neutral</option>
                    <option value="tense">Tense</option>
                    <option value="joyful">Joyful</option>
                    <option value="melancholic">Melancholic</option>
                    <option value="suspenseful">Suspenseful</option>
                    <option value="romantic">Romantic</option>
                  </select>
                </div>

                <Input
                  label="Target Word Count"
                  type="number"
                  min={100}
                  max={3000}
                  value={formData.targetWordCount}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      targetWordCount: parseInt(e.target.value),
                    })
                  }
                  helperText="Between 100 and 3000 words"
                />
              </div>
            </Card>
          )}

          {/* Generate Button */}
          {selectedCharacters.length > 0 && (
            <div className="flex justify-end">
              <Button
                onClick={handleGenerate}
                isLoading={generating}
                disabled={
                  !selectedSourceId ||
                  selectedCharacters.length === 0 ||
                  !formData.sceneDescription ||
                  !formData.setting
                }
                size="lg"
              >
                <Wand2 className="h-5 w-5 mr-2" />
                Generate Scene
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
