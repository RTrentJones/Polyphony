/**
 * Zustand stores (auth, sources, scenes).
 *
 * Security: the access token and user live in memory only — nothing is
 * persisted to localStorage. Sessions survive reloads via the backend's
 * httpOnly refresh cookie: on first protected render, loadUser() hits
 * /auth/me and the api-client interceptor transparently refreshes.
 */

import { create } from 'zustand'
import apiClient, { normalizeSource, toApiError } from './api-client'
import type {
  AuthTokenResponse,
  Character,
  LoginCredentials,
  RegisterData,
  Scene,
  SceneGenerateResponse,
  SceneListResponse,
  SceneRequest,
  Source,
  SourceCharactersResponse,
  SourceListResponse,
  SourceUploadResponse,
  User,
} from './types'

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

interface AuthState {
  user: User | null
  accessToken: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (credentials: LoginCredentials) => Promise<void>
  register: (data: RegisterData) => Promise<void>
  logout: () => Promise<void>
  /** Fetch /auth/me and mark the session authenticated; throws on failure. */
  fetchUser: () => Promise<void>
  /** Alias used by ProtectedRoute. */
  loadUser: () => Promise<void>
  setAccessToken: (token: string | null) => void
  /** Drop all auth state (used by the api-client on refresh failure). */
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  isAuthenticated: false,
  isLoading: false,

  login: async ({ username, password }) => {
    set({ isLoading: true })
    try {
      // OAuth2 password form: application/x-www-form-urlencoded.
      const body = new URLSearchParams({ username, password })
      const { data } = await apiClient.post<AuthTokenResponse>('/auth/login', body)
      set({ accessToken: data.access_token })
      const me = await apiClient.get<User>('/auth/me')
      set({ user: me.data, isAuthenticated: true, isLoading: false })
    } catch (err) {
      set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false })
      throw toApiError(err)
    }
  },

  register: async (data) => {
    set({ isLoading: true })
    try {
      const res = await apiClient.post<AuthTokenResponse>('/auth/register', data)
      set({
        accessToken: res.data.access_token,
        user: res.data.user ? { ...res.data.user } : null,
        isAuthenticated: true,
        isLoading: false,
      })
    } catch (err) {
      set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false })
      throw toApiError(err)
    }
  },

  logout: async () => {
    // Clear locally first so the UI never shows a stale session.
    get().clearAuth()
    try {
      await apiClient.post('/auth/logout')
    } catch {
      // Best effort — the refresh cookie is revoked server-side when reachable.
    }
  },

  fetchUser: async () => {
    set({ isLoading: true })
    try {
      // With no/expired access token this 401s and the api-client interceptor
      // silently exchanges the refresh cookie for a new token, then retries.
      const { data } = await apiClient.get<User>('/auth/me')
      set({ user: data, isAuthenticated: true, isLoading: false })
    } catch (err) {
      set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false })
      throw toApiError(err)
    }
  },

  loadUser: async () => {
    await get().fetchUser()
  },

  setAccessToken: (token) => set({ accessToken: token }),

  clearAuth: () =>
    set({ user: null, accessToken: null, isAuthenticated: false, isLoading: false }),
}))

// ---------------------------------------------------------------------------
// Sources (was Manuscripts; book-rooted — docs/ADR-002-book-as-root.md §2)
// ---------------------------------------------------------------------------

interface SourceState {
  sources: Source[]
  isLoading: boolean
  error: string | null
  fetchSources: (bookId?: string) => Promise<void>
  /** Upload into a book; omitting bookId auto-creates one server-side. */
  uploadSource: (
    file: File,
    title: string,
    author?: string,
    bookId?: string
  ) => Promise<Source>
  deleteSource: (id: string) => Promise<void>
  fetchCharacters: (sourceId: string) => Promise<Character[]>
}

export const useSourceStore = create<SourceState>((set) => ({
  sources: [],
  isLoading: false,
  error: null,

  fetchSources: async (bookId) => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<SourceListResponse>('/sources/', {
        params: bookId ? { book_id: bookId } : undefined,
      })
      set({ sources: data.sources.map(normalizeSource), isLoading: false })
    } catch (err) {
      const apiError = toApiError(err)
      set({ isLoading: false, error: apiError.message })
      throw apiError
    }
  },

  uploadSource: async (file, title, author, bookId) => {
    try {
      const formData = new FormData()
      formData.append('file', file)
      // title/author/book_id are query parameters on the backend endpoint.
      const { data } = await apiClient.post<SourceUploadResponse>(
        '/sources/upload',
        formData,
        {
          params: {
            title,
            ...(author ? { author } : {}),
            ...(bookId ? { book_id: bookId } : {}),
          },
        }
      )
      const source = normalizeSource({ ...data, uploaded_at: null })
      set((state) => ({ sources: [source, ...state.sources] }))
      return source
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteSource: async (id) => {
    try {
      await apiClient.delete(`/sources/${id}`)
      set((state) => ({
        sources: state.sources.filter((s) => s.id !== id),
      }))
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchCharacters: async (sourceId) => {
    try {
      const { data } = await apiClient.get<SourceCharactersResponse>(
        `/sources/${sourceId}/characters`
      )
      return data.characters
    } catch (err) {
      throw toApiError(err)
    }
  },
}))

// ---------------------------------------------------------------------------
// Scenes
// ---------------------------------------------------------------------------

interface SceneState {
  scenes: Scene[]
  currentScene: Scene | null
  isLoading: boolean
  error: string | null
  fetchScenes: (sourceId?: string) => Promise<void>
  fetchScene: (id: string) => Promise<Scene>
  generateScene: (request: SceneRequest) => Promise<SceneGenerateResponse>
  deleteScene: (id: string) => Promise<void>
}

export const useSceneStore = create<SceneState>((set) => ({
  scenes: [],
  currentScene: null,
  isLoading: false,
  error: null,

  fetchScenes: async (sourceId) => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<SceneListResponse>('/scenes/', {
        params: sourceId ? { source_id: sourceId } : undefined,
      })
      set({ scenes: data.scenes, isLoading: false })
    } catch (err) {
      const apiError = toApiError(err)
      set({ isLoading: false, error: apiError.message })
      throw apiError
    }
  },

  fetchScene: async (id) => {
    try {
      const { data } = await apiClient.get<Scene>(`/scenes/${id}`)
      set({ currentScene: data })
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  generateScene: async (request) => {
    try {
      const { data } = await apiClient.post<SceneGenerateResponse>(
        '/scenes/generate',
        request
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteScene: async (id) => {
    try {
      await apiClient.delete(`/scenes/${id}`)
      set((state) => ({
        scenes: state.scenes.filter((s) => s.id !== id),
        currentScene: state.currentScene?.id === id ? null : state.currentScene,
      }))
    } catch (err) {
      throw toApiError(err)
    }
  },
}))
