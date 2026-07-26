/**
 * Characters Page — your character bible: create characters from scratch,
 * seed their voice with pasted samples, and test how they sound.
 */

'use client'

import { useCallback, useEffect, useState } from 'react'
import { MessageSquare, Mic, Plus, Trash2, Users } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import Input from '@/components/Input'
import Loading from '@/components/Loading'
import Modal from '@/components/Modal'
import apiClient, { toApiError } from '@/lib/api-client'
import type { Character } from '@/lib/types'

const CHUNK_TYPES = ['dialogue', 'action', 'thought'] as const

export default function CharactersPage() {
  const [characters, setCharacters] = useState<Character[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Create modal
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [createData, setCreateData] = useState({ name: '', role: '', description: '' })
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  // Voice-samples modal
  const [voiceTarget, setVoiceTarget] = useState<Character | null>(null)
  const [voiceText, setVoiceText] = useState('')
  const [voiceType, setVoiceType] = useState<(typeof CHUNK_TYPES)[number]>('dialogue')
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [voiceResult, setVoiceResult] = useState<string | null>(null)

  // Test-voice modal
  const [testTarget, setTestTarget] = useState<Character | null>(null)
  const [testPrompt, setTestPrompt] = useState('')
  const [testError, setTestError] = useState<string | null>(null)
  const [testBusy, setTestBusy] = useState(false)
  const [testReply, setTestReply] = useState<string | null>(null)

  const fetchCharacters = useCallback(async () => {
    try {
      const { data } = await apiClient.get<{ characters: Character[] }>('/characters/')
      setCharacters(data.characters)
      setLoadError(null)
    } catch (err) {
      setLoadError(toApiError(err).message)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCharacters()
  }, [fetchCharacters])

  const closeCreateModal = () => {
    setCreateModalOpen(false)
    setCreateData({ name: '', role: '', description: '' })
    setCreateError(null)
  }

  const handleCreate = async () => {
    if (!createData.name.trim()) {
      setCreateError('Please give the character a name')
      return
    }
    setCreateError(null)
    setCreating(true)
    try {
      await apiClient.post('/characters/', {
        name: createData.name.trim(),
        ...(createData.role.trim() ? { role: createData.role.trim() } : {}),
        ...(createData.description.trim()
          ? { description: createData.description.trim() }
          : {}),
      })
      closeCreateModal()
      await fetchCharacters()
    } catch (err) {
      setCreateError(toApiError(err).message)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (character: Character) => {
    if (
      confirm(
        `Delete "${character.name}"? This also removes their indexed voice samples.`
      )
    ) {
      try {
        await apiClient.delete(`/characters/${character.id}`)
        await fetchCharacters()
      } catch (err) {
        alert(toApiError(err).message)
      }
    }
  }

  const closeVoiceModal = () => {
    setVoiceTarget(null)
    setVoiceText('')
    setVoiceType('dialogue')
    setVoiceError(null)
    setVoiceResult(null)
  }

  const handleAddVoice = async () => {
    if (!voiceTarget) return
    const samples = voiceText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    if (samples.length === 0) {
      setVoiceError('Paste at least one line — each line becomes one voice sample')
      return
    }
    setVoiceError(null)
    setVoiceBusy(true)
    try {
      const { data } = await apiClient.post<{ indexed: number }>(
        `/characters/${voiceTarget.id}/voice-samples`,
        { samples, chunk_type: voiceType }
      )
      setVoiceResult(`Indexed ${data.indexed} sample${data.indexed === 1 ? '' : 's'}`)
      setVoiceText('')
      await fetchCharacters()
    } catch (err) {
      setVoiceError(toApiError(err).message)
    } finally {
      setVoiceBusy(false)
    }
  }

  const closeTestModal = () => {
    setTestTarget(null)
    setTestPrompt('')
    setTestError(null)
    setTestReply(null)
  }

  const handleTestVoice = async () => {
    if (!testTarget || testPrompt.trim().length < 3) {
      setTestError('Give the character something to react to (a few words at least)')
      return
    }
    setTestError(null)
    setTestBusy(true)
    setTestReply(null)
    try {
      const { data } = await apiClient.post<{ dialogue?: string; text?: string }>(
        `/characters/${testTarget.id}/test-dialogue`,
        { prompt: testPrompt.trim() }
      )
      setTestReply(data.dialogue || data.text || JSON.stringify(data))
    } catch (err) {
      setTestError(toApiError(err).message)
    } finally {
      setTestBusy(false)
    }
  }

  if (isLoading && characters.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading characters..." />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Characters</h1>
          <p className="text-gray-600">
            Your character bible — created by hand or extracted from sources
          </p>
        </div>
        <Button onClick={() => setCreateModalOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          New Character
        </Button>
      </div>

      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
          <p className="text-sm text-red-600">{loadError}</p>
        </div>
      )}

      {/* Characters Grid */}
      {characters.length === 0 ? (
        <Card className="text-center py-12">
          <Users className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No characters yet</h3>
          <p className="text-gray-600 mb-6">
            Create one from scratch and paste a few lines in their voice — or upload a
            source to extract its cast automatically
          </p>
          <Button onClick={() => setCreateModalOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            New Character
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {characters.map((character) => (
            <Card key={character.id} className="flex flex-col">
              <div className="flex-1">
                <div className="flex items-start justify-between mb-3">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <Users className="h-6 w-6 text-primary-600" />
                  </div>
                  <span className="px-2 py-1 text-xs font-medium rounded-full bg-gray-100 text-gray-700">
                    {character.role || (character.source_id ? 'extracted' : 'manual')}
                  </span>
                </div>

                <h3 className="text-lg font-semibold text-gray-900 mb-2">
                  {character.name}
                </h3>

                <div className="space-y-1 text-sm text-gray-600 mb-4">
                  {character.description && <p>{character.description}</p>}
                  <p>
                    {character.indexed_at
                      ? `${character.dialogue_count || 0} voice samples indexed`
                      : 'No voice indexed yet — add samples so scenes sound like them'}
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-2 pt-4 border-t border-gray-200">
                <Button
                  variant="outline"
                  size="sm"
                  fullWidth
                  onClick={() => setVoiceTarget(character)}
                >
                  <Mic className="h-4 w-4 mr-2" />
                  Add Voice
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  fullWidth
                  onClick={() => setTestTarget(character)}
                  disabled={!character.indexed_at}
                >
                  <MessageSquare className="h-4 w-4 mr-2" />
                  Test
                </Button>
                <Button variant="ghost" size="sm" onClick={() => handleDelete(character)}>
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
        title="New Character"
        size="md"
      >
        <div className="space-y-4">
          {createError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{createError}</p>
            </div>
          )}

          <Input
            label="Name"
            placeholder="The character's name"
            value={createData.name}
            onChange={(e) => setCreateData({ ...createData, name: e.target.value })}
            required
          />

          <Input
            label="Role"
            placeholder="e.g., protagonist, antagonist (optional)"
            value={createData.role}
            onChange={(e) => setCreateData({ ...createData, role: e.target.value })}
          />

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Description
            </label>
            <textarea
              value={createData.description}
              onChange={(e) =>
                setCreateData({ ...createData, description: e.target.value })
              }
              placeholder="Who they are, what drives them (optional)"
              rows={3}
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
              disabled={!createData.name.trim()}
            >
              Create Character
            </Button>
          </div>
        </div>
      </Modal>

      {/* Voice-samples Modal */}
      <Modal
        isOpen={voiceTarget !== null}
        onClose={closeVoiceModal}
        title={voiceTarget ? `Add voice samples — ${voiceTarget.name}` : ''}
        size="lg"
      >
        <div className="space-y-4">
          {voiceError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{voiceError}</p>
            </div>
          )}
          {voiceResult && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <p className="text-sm text-green-700">{voiceResult}</p>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Samples — one per line
            </label>
            <textarea
              value={voiceText}
              onChange={(e) => setVoiceText(e.target.value)}
              placeholder={
                'Paste lines the character has said or would say, one per line.\nEach line is embedded and used to keep their voice consistent.'
              }
              rows={8}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent font-mono text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Sample type
            </label>
            <select
              value={voiceType}
              onChange={(e) =>
                setVoiceType(e.target.value as (typeof CHUNK_TYPES)[number])
              }
              className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              {CHUNK_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center space-x-3 pt-4">
            <Button variant="outline" fullWidth onClick={closeVoiceModal}>
              Done
            </Button>
            <Button
              fullWidth
              onClick={handleAddVoice}
              isLoading={voiceBusy}
              disabled={!voiceText.trim()}
            >
              <Mic className="h-4 w-4 mr-2" />
              Index Samples
            </Button>
          </div>
        </div>
      </Modal>

      {/* Test-voice Modal */}
      <Modal
        isOpen={testTarget !== null}
        onClose={closeTestModal}
        title={testTarget ? `Test voice — ${testTarget.name}` : ''}
        size="md"
      >
        <div className="space-y-4">
          {testError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-600">{testError}</p>
            </div>
          )}

          <Input
            label="Prompt"
            placeholder='e.g., "React to finding the map is a forgery"'
            value={testPrompt}
            onChange={(e) => setTestPrompt(e.target.value)}
          />

          {testReply && (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{testReply}</p>
            </div>
          )}

          <div className="flex items-center space-x-3 pt-4">
            <Button variant="outline" fullWidth onClick={closeTestModal}>
              Close
            </Button>
            <Button
              fullWidth
              onClick={handleTestVoice}
              isLoading={testBusy}
              disabled={testPrompt.trim().length < 3}
            >
              <MessageSquare className="h-4 w-4 mr-2" />
              Generate Line
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
