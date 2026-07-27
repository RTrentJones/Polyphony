/**
 * Book-rooted Canon operations: version history/restore for any versioned entity,
 * canon-entry CRUD, style-guide upsert, and character edit/delete.
 *
 * Backend: app/api/versioning.py (list/get/restore), app/api/canon.py (entries +
 * style), app/api/characters.py (book-scoped list, detail, edit, delete).
 */

import apiClient, { toApiError } from './api-client'
import type {
  CanonEntry,
  CanonResponse,
  Character,
  CharacterDetail,
  CharacterUpdateData,
  EntityVersionDetail,
  EntityVersionSummary,
  StyleGuide,
  VersionedEntityType,
} from './types'

export const canonApi = {
  // --- Version history / restore (any versioned entity) ----------------------

  async listVersions(
    bookId: string,
    entityType: VersionedEntityType,
    entityId: string
  ): Promise<EntityVersionSummary[]> {
    try {
      const { data } = await apiClient.get<{ versions: EntityVersionSummary[] }>(
        `/books/${bookId}/versions/${entityType}/${entityId}`
      )
      return data.versions
    } catch (err) {
      throw toApiError(err)
    }
  },

  async getVersion(
    bookId: string,
    entityType: VersionedEntityType,
    entityId: string,
    versionNo: number
  ): Promise<EntityVersionDetail> {
    try {
      const { data } = await apiClient.get<EntityVersionDetail>(
        `/books/${bookId}/versions/${entityType}/${entityId}/${versionNo}`
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  /** Restore is forward-only: it appends a new head version carrying the old
   *  content and sets the live row (docs/ADR-002 §5). */
  async restoreVersion(
    bookId: string,
    entityType: VersionedEntityType,
    entityId: string,
    versionNo: number
  ): Promise<void> {
    try {
      await apiClient.post(
        `/books/${bookId}/versions/${entityType}/${entityId}/restore/${versionNo}`
      )
    } catch (err) {
      throw toApiError(err)
    }
  },

  // --- Canon (entries + style) -----------------------------------------------

  async getCanon(bookId: string): Promise<CanonResponse> {
    try {
      const { data } = await apiClient.get<CanonResponse>(`/books/${bookId}/canon`)
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  async createEntry(
    bookId: string,
    entry: { name: string; category: string; content?: string }
  ): Promise<CanonEntry> {
    try {
      const { data } = await apiClient.post<CanonEntry>(
        `/books/${bookId}/canon/entries`,
        entry
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  async updateEntry(
    bookId: string,
    entryId: string,
    patch: Partial<{ name: string; category: string; content: string }>
  ): Promise<CanonEntry> {
    try {
      const { data } = await apiClient.patch<CanonEntry>(
        `/books/${bookId}/canon/entries/${entryId}`,
        patch
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  async deleteEntry(bookId: string, entryId: string): Promise<void> {
    try {
      await apiClient.delete(`/books/${bookId}/canon/entries/${entryId}`)
    } catch (err) {
      throw toApiError(err)
    }
  },

  async upsertStyle(bookId: string, style: Partial<StyleGuide>): Promise<StyleGuide> {
    try {
      const { data } = await apiClient.put<StyleGuide>(
        `/books/${bookId}/canon/style`,
        style
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  // --- Characters (book-scoped) ----------------------------------------------

  async listCharacters(bookId: string): Promise<Character[]> {
    try {
      const { data } = await apiClient.get<{ characters: Character[] }>('/characters/')
      return data.characters.filter((c) => c.book_id === bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  async getCharacter(id: string): Promise<CharacterDetail> {
    try {
      const { data } = await apiClient.get<CharacterDetail>(`/characters/${id}`)
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  async createCharacter(
    bookId: string,
    fields: { name: string; role?: string; description?: string }
  ): Promise<Character> {
    try {
      const { data } = await apiClient.post<Character>('/characters/', {
        ...fields,
        book_id: bookId,
      })
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  async updateCharacter(id: string, patch: CharacterUpdateData): Promise<void> {
    try {
      await apiClient.patch(`/characters/${id}`, patch)
    } catch (err) {
      throw toApiError(err)
    }
  },

  async deleteCharacter(id: string): Promise<void> {
    try {
      await apiClient.delete(`/characters/${id}`)
    } catch (err) {
      throw toApiError(err)
    }
  },
}
