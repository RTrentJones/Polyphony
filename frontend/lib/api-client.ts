/**
 * API client for the Polyphony FastAPI backend (/api/v1, same origin —
 * next.config.js rewrites /api/* to the backend in dev).
 *
 * Auth model:
 * - The short-lived access token lives in memory only (zustand auth store),
 *   never in localStorage; a request interceptor injects it as a Bearer header.
 * - The rotating refresh token is an httpOnly cookie set by the backend
 *   (scoped to /api/v1/auth), so `withCredentials: true` is all we need.
 * - On a 401 (outside the auth endpoints) the response interceptor calls
 *   POST /auth/refresh once (single-flight across concurrent 401s), stores
 *   the new access token, and retries the original request. If the refresh
 *   fails, the auth store is cleared and the user is sent to /auth/login.
 *
 * The store module is imported lazily inside the interceptors to stay safe
 * against the api-client <-> store circular dependency.
 */

import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type {
  ApiError,
  ApiSource,
  AuthTokenResponse,
  Character,
  Source,
  SourceCharactersResponse,
} from './types'

const AUTH_PATHS_WITHOUT_REFRESH = ['/auth/login', '/auth/register', '/auth/refresh']

const api = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

/** Normalize any thrown value into the ApiError shape the pages consume. */
export function toApiError(err: unknown): ApiError {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    let message = err.message
    if (typeof detail === 'string' && detail) {
      message = detail
    } else if (Array.isArray(detail) && detail.length > 0) {
      // FastAPI validation errors: [{ loc, msg, type }, ...]
      const first = detail[0] as { msg?: string }
      if (first?.msg) message = first.msg
    } else if (!err.response) {
      message = 'Could not reach the server. Please try again.'
    }
    return { message, status: err.response?.status, detail }
  }
  if (err instanceof Error) return { message: err.message }
  return { message: 'An unexpected error occurred' }
}

/**
 * Map a raw API source to the shape the UI reads
 * (created_at <- uploaded_at, processing_status <- status).
 */
export function normalizeSource(
  raw: ApiSource & Partial<Pick<Source, 'character_count'>>
): Source {
  return {
    ...raw,
    created_at: raw.uploaded_at ?? new Date().toISOString(),
    processing_status: raw.status,
  }
}

// ---------------------------------------------------------------------------
// Interceptors
// ---------------------------------------------------------------------------

api.interceptors.request.use(async (config) => {
  // Lazy import: circular-safe (store.ts imports this module).
  const { useAuthStore } = await import('./store')
  const token = useAuthStore.getState().accessToken
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/** Single-flight refresh: concurrent 401s share one /auth/refresh call. */
let refreshPromise: Promise<string> | null = null

function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    // Bare axios (not `api`) so a failing refresh never re-enters this interceptor.
    refreshPromise = axios
      .post<AuthTokenResponse>('/api/v1/auth/refresh', null, { withCredentials: true })
      .then((res) => res.data.access_token)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined
    const url = original?.url ?? ''
    const isAuthEndpoint = AUTH_PATHS_WITHOUT_REFRESH.some((p) => url.includes(p))

    if (error.response?.status === 401 && original && !original._retry && !isAuthEndpoint) {
      original._retry = true
      try {
        const token = await refreshAccessToken()
        const { useAuthStore } = await import('./store')
        useAuthStore.getState().setAccessToken(token)
        original.headers.Authorization = `Bearer ${token}`
        return api(original)
      } catch {
        const { useAuthStore } = await import('./store')
        useAuthStore.getState().clearAuth()
        if (
          typeof window !== 'undefined' &&
          !window.location.pathname.startsWith('/auth/')
        ) {
          window.location.href = '/auth/login'
        }
        return Promise.reject(error)
      }
    }

    return Promise.reject(error)
  }
)

// ---------------------------------------------------------------------------
// Typed convenience methods (used directly by pages)
// ---------------------------------------------------------------------------

async function getSource(sourceId: string): Promise<Source> {
  try {
    const { data } = await api.get<ApiSource>(`/sources/${sourceId}`)
    return normalizeSource(data)
  } catch (err) {
    throw toApiError(err)
  }
}

async function getSourceCharacters(sourceId: string): Promise<Character[]> {
  try {
    const { data } = await api.get<SourceCharactersResponse>(
      `/sources/${sourceId}/characters`
    )
    return data.characters
  } catch (err) {
    throw toApiError(err)
  }
}

/** The axios instance, augmented with typed helpers the pages call. */
const apiClient = Object.assign(api, {
  getSource,
  getSourceCharacters,
})

export default apiClient
