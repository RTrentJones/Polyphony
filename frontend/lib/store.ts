/**
 * Zustand stores (auth, manuscripts, scenes).
 *
 * Security: the access token and user live in memory only — nothing is
 * persisted to localStorage. Sessions survive reloads via the backend's
 * httpOnly refresh cookie: on first protected render, loadUser() hits
 * /auth/me and the api-client interceptor transparently refreshes.
 */

import { create } from 'zustand'
import apiClient, { normalizeManuscript, toApiError } from './api-client'
import type {
  AuthTokenResponse,
  Character,
  LoginCredentials,
  Manuscript,
  ManuscriptCharactersResponse,
  ManuscriptListResponse,
  ManuscriptUploadResponse,
  RegisterData,
  Scene,
  SceneGenerateResponse,
  SceneListResponse,
  SceneRequest,
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
// Manuscripts
// ---------------------------------------------------------------------------

interface ManuscriptState {
  manuscripts: Manuscript[]
  isLoading: boolean
  error: string | null
  fetchManuscripts: () => Promise<void>
  uploadManuscript: (file: File, title: string, author?: string) => Promise<Manuscript>
  deleteManuscript: (id: string) => Promise<void>
  fetchCharacters: (manuscriptId: string) => Promise<Character[]>
}

export const useManuscriptStore = create<ManuscriptState>((set) => ({
  manuscripts: [],
  isLoading: false,
  error: null,

  fetchManuscripts: async () => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<ManuscriptListResponse>('/manuscripts/')
      set({ manuscripts: data.manuscripts.map(normalizeManuscript), isLoading: false })
    } catch (err) {
      const apiError = toApiError(err)
      set({ isLoading: false, error: apiError.message })
      throw apiError
    }
  },

  uploadManuscript: async (file, title, author) => {
    try {
      const formData = new FormData()
      formData.append('file', file)
      // title/author are query parameters on the backend endpoint.
      const { data } = await apiClient.post<ManuscriptUploadResponse>(
        '/manuscripts/upload',
        formData,
        { params: { title, ...(author ? { author } : {}) } }
      )
      const manuscript = normalizeManuscript({ ...data, uploaded_at: null })
      set((state) => ({ manuscripts: [manuscript, ...state.manuscripts] }))
      return manuscript
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteManuscript: async (id) => {
    try {
      await apiClient.delete(`/manuscripts/${id}`)
      set((state) => ({
        manuscripts: state.manuscripts.filter((m) => m.id !== id),
      }))
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchCharacters: async (manuscriptId) => {
    try {
      const { data } = await apiClient.get<ManuscriptCharactersResponse>(
        `/manuscripts/${manuscriptId}/characters`
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
  fetchScenes: (manuscriptId?: string) => Promise<void>
  fetchScene: (id: string) => Promise<Scene>
  generateScene: (request: SceneRequest) => Promise<SceneGenerateResponse>
  deleteScene: (id: string) => Promise<void>
}

export const useSceneStore = create<SceneState>((set) => ({
  scenes: [],
  currentScene: null,
  isLoading: false,
  error: null,

  fetchScenes: async (manuscriptId) => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<SceneListResponse>('/scenes/', {
        params: manuscriptId ? { manuscript_id: manuscriptId } : undefined,
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
