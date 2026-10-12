/**
 * A book's Trash (SPEC-13): soft-deleted characters, canon entries, sources,
 * chapters and scenes, restorable with what they owned until purged.
 *
 * Backend: app/api/trash.py.
 */

import apiClient, { toApiError } from './api-client'
import type { TrashEntityType, TrashItem } from './types'

export const trashApi = {
  async list(bookId: string): Promise<TrashItem[]> {
    try {
      const { data } = await apiClient.get<{ items: TrashItem[] }>(`/books/${bookId}/trash`)
      return data.items
    } catch (err) {
      throw toApiError(err)
    }
  },

  /** 409 (ApiError.detail: TrashConflictDetail) when a live entity holds its
   *  name, or a scene's chapter is still in the Trash. */
  async restore(bookId: string, type: TrashEntityType, id: string): Promise<void> {
    try {
      await apiClient.post(`/books/${bookId}/trash/${type}/${id}/restore`)
    } catch (err) {
      throw toApiError(err)
    }
  },

  /** Permanent. The UI asks for the name typed out first. */
  async purge(bookId: string, type: TrashEntityType, id: string): Promise<void> {
    try {
      await apiClient.delete(`/books/${bookId}/trash/${type}/${id}`)
    } catch (err) {
      throw toApiError(err)
    }
  },
}
