/**
 * Voice workbench + grounding provenance (SPEC-11).
 *
 * Backend: app/api/characters.py (chunk list/edit/delete, voice samples,
 * retrieval inspector, test dialogue) and app/api/grounding.py (which chunks
 * grounded a generated scene).
 */

import apiClient, { toApiError } from './api-client'
import type { RetrievalHit, VoiceChunk } from './types'

export interface ChunkPage {
  character_id: string
  chunks: VoiceChunk[]
  total: number
  skip: number
  limit: number
}

export interface RetrievalResult {
  character_id: string
  query: string
  /** RAG_SCORE_THRESHOLD: hits below it ground generation only if none clear it. */
  threshold: number
  results: RetrievalHit[]
}

/** One grounding entry, recorded as a scene records it (`grounding_entries`). */
export interface DialogueGroundingSample {
  character_id: string
  chunk_id: string | null
  score: number | null
  /** The sample's wording, bounded (~300 chars). */
  excerpt?: string | null
  /** Why no sample grounded the line (e.g. "no indexed voice samples"). */
  reason?: string | null
}

export interface DialogueTestResult {
  character: string
  dialogue: string
  action?: string | null
  confidence_score?: number | null
  grounding?: DialogueGroundingSample[]
}

export interface GroundingSample {
  chunk_id: string | null
  score: number | null
  deleted: boolean
  text: string | null
  chunk_type: string | null
  source: string | null
  /** The sample's wording when the scene was generated (bounded, ~300 chars);
   *  shown for a sample deleted since. Absent on older scenes. */
  excerpt?: string | null
}

export interface SceneGroundingResponse {
  scene_id: string
  mode: string | null
  /** False for scenes generated before grounding was recorded. */
  recorded: boolean
  beats: {
    beat_index: number
    description: string | null
    characters: {
      character_id: string
      name: string | null
      samples: GroundingSample[]
      /** Why no sample grounded this character (e.g. "no indexed voice
       *  samples", "context only"); null when samples did. */
      reason?: string | null
    }[]
  }[]
}

async function call<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await fn()).data
  } catch (err) {
    throw toApiError(err)
  }
}

export const voiceApi = {
  listChunks(
    characterId: string,
    params: { skip?: number; limit?: number; chunk_type?: string; source_id?: string } = {}
  ): Promise<ChunkPage> {
    return call(() =>
      apiClient.get<ChunkPage>(`/characters/${characterId}/chunks`, { params })
    )
  },

  /** Re-embeds server-side, so retrieval ranks the new text. */
  async updateChunk(characterId: string, chunkId: string, text: string): Promise<void> {
    await call(() =>
      apiClient.patch(`/characters/${characterId}/chunks/${chunkId}`, { text })
    )
  },

  async deleteChunk(characterId: string, chunkId: string): Promise<void> {
    await call(() => apiClient.delete(`/characters/${characterId}/chunks/${chunkId}`))
  },

  addSamples(
    characterId: string,
    samples: string[],
    chunkType = 'dialogue'
  ): Promise<{ id: string; indexed: number }> {
    return call(() =>
      apiClient.post(`/characters/${characterId}/voice-samples`, {
        samples,
        chunk_type: chunkType,
      })
    )
  },

  retrieve(characterId: string, query: string, k = 8): Promise<RetrievalResult> {
    return call(() =>
      apiClient.post<RetrievalResult>(`/characters/${characterId}/retrieve`, {
        query,
        k,
      })
    )
  },

  testLine(characterId: string, prompt: string): Promise<DialogueTestResult> {
    return call(() =>
      apiClient.post<DialogueTestResult>(`/characters/${characterId}/test-dialogue`, {
        prompt,
      })
    )
  },

  sceneGrounding(sceneId: string): Promise<SceneGroundingResponse> {
    return call(() =>
      apiClient.get<SceneGroundingResponse>(`/books/scenes/${sceneId}/grounding`)
    )
  },
}

/** Paste → samples: one per paragraph (blank-line separated), trimmed. */
export function splitSamples(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((s) => s.trim())
    .filter(Boolean)
}
