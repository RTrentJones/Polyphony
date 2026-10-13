/**
 * "Why does it sound like this?" — the voice samples that grounded a generated
 * scene, per beat and per character, with their retrieval scores (R5.3).
 *
 * Fetched only when opened: it is provenance, not something every read needs.
 * Text is the chunk's CURRENT text; a sample deleted since generation shows
 * the excerpt stored when the scene was written, marked as deleted. A character
 * no sample grounded is listed with the reason (none indexed, context only).
 */

'use client'

import { useState } from 'react'
import { formatScore } from './VoicePanel'
import { voiceApi, type SceneGroundingResponse } from '@/lib/voice-api'

export default function SceneGrounding({ sceneId }: { sceneId: string }) {
  const [data, setData] = useState<SceneGroundingResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    if (data || loading) return
    setLoading(true)
    try {
      setData(await voiceApi.sceneGrounding(sceneId))
    } catch (err: any) {
      setError(err.message || 'Failed to load grounding')
    } finally {
      setLoading(false)
    }
  }

  return (
    <details
      className="mt-3 text-sm"
      onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && load()}
    >
      <summary className="cursor-pointer text-primary-700 select-none">
        Why does it sound like this?
      </summary>
      <div className="mt-2 space-y-3" data-testid="scene-grounding">
        {loading && <p className="text-gray-500">Loading…</p>}
        {error && <p className="text-red-600">{error}</p>}
        {data && !data.recorded && (
          <p className="text-gray-500">
            This scene was generated before grounding was recorded.
          </p>
        )}
        {data &&
          data.recorded &&
          data.beats.every((b) => b.characters.every((c) => c.samples.length === 0)) && (
            <p className="text-gray-500">
              No voice samples grounded this scene — its characters had none indexed.
            </p>
          )}
        {data?.beats
          .filter((b) => b.characters.length > 0)
          .map((beat) => (
            <div key={beat.beat_index}>
              <p className="font-medium text-gray-800">
                Beat {beat.beat_index + 1}
                {beat.description && (
                  <span className="font-normal text-gray-600"> — {beat.description}</span>
                )}
              </p>
              {beat.characters.map((c) => (
                <div key={c.character_id} className="ml-3 mt-1">
                  <p className="text-gray-700">
                    {c.name || 'Deleted character'}
                    {c.samples.length === 0 && (
                      <span className="ml-2 text-xs italic text-gray-500">
                        ({c.reason || 'no indexed voice samples'})
                      </span>
                    )}
                  </p>
                  <ul className="ml-3 space-y-0.5">
                    {c.samples.map((s, i) => (
                      <li key={s.chunk_id || i} className="text-xs flex gap-2">
                        <span className="font-mono text-gray-600 w-12 shrink-0">
                          {formatScore(s.score)}
                        </span>
                        {s.deleted ? (
                          <span className="text-gray-500">
                            {s.excerpt && <span>{s.excerpt}</span>}
                            <span className="ml-1 italic text-gray-400">
                              (deleted since generation)
                            </span>
                          </span>
                        ) : (
                          <span className="text-gray-800">{s.text}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ))}
      </div>
    </details>
  )
}
