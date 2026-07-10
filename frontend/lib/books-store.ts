/**
 * Zustand store + typed API calls for the book-writing workflow:
 * books, chapters, scenes-in-chapters, plans (outline/beat sheet),
 * plot threads, and continuity reports.
 *
 * Backend surface: app/api/books.py (mounted at /books) and
 * app/api/plans.py (mounted at /api/v1 root: /books/{id}/plans,
 * /threads/{id}, /books/{id}/continuity).
 */

import { create } from 'zustand'
import apiClient, { toApiError } from './api-client'
import type {
  BookCreateData,
  BookDetail,
  BookExportFormat,
  BookListResponse,
  BookPlan,
  BookPlanListResponse,
  BookSummary,
  BookUpdateData,
  ChapterCreateData,
  ChapterDetail,
  ChapterSceneGenerateResponse,
  ChapterSceneRequest,
  ChapterUpdateData,
  ContinuityReport,
  ContinuityReportListResponse,
  ContinuityStartResponse,
  PlanKind,
  PlanNode,
  PlotThread,
  PlotThreadListResponse,
  PromoteNodeResponse,
  Scene,
  SceneContentSaveResponse,
  SceneRevisionsResponse,
  ThreadCreateData,
  ThreadEventCreateData,
  ThreadUpdateData,
} from './types'

// ---------------------------------------------------------------------------
// Typed API helpers (thin wrappers; all throw the normalized ApiError)
// ---------------------------------------------------------------------------

export const booksApi = {
  // -- Scenes shared with the classic scene endpoints ------------------------

  /** GET /scenes/{id} — used to poll generation status. */
  async getScene(sceneId: string): Promise<Scene> {
    try {
      const { data } = await apiClient.get<Scene>(`/scenes/${sceneId}`)
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  // -- Draft editing ----------------------------------------------------------

  /** PUT /books/scenes/{id}/content */
  async saveSceneContent(
    sceneId: string,
    content: string
  ): Promise<SceneContentSaveResponse> {
    try {
      const { data } = await apiClient.put<SceneContentSaveResponse>(
        `/books/scenes/${sceneId}/content`,
        { content }
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  /** GET /books/scenes/{id}/revisions */
  async getSceneRevisions(sceneId: string): Promise<SceneRevisionsResponse> {
    try {
      const { data } = await apiClient.get<SceneRevisionsResponse>(
        `/books/scenes/${sceneId}/revisions`
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  /** PATCH /books/scenes/{id}/position */
  async moveScene(sceneId: string, position: number): Promise<void> {
    try {
      await apiClient.patch(`/books/scenes/${sceneId}/position`, { position })
    } catch (err) {
      throw toApiError(err)
    }
  },

  // -- Export -----------------------------------------------------------------

  /**
   * GET /books/{id}/export?format=... as a blob and trigger a browser
   * file download (filename from Content-Disposition when present).
   */
  async exportBook(
    bookId: string,
    format: BookExportFormat,
    fallbackTitle: string
  ): Promise<void> {
    try {
      const res = await apiClient.get<Blob>(`/books/${bookId}/export`, {
        params: { format },
        responseType: 'blob',
      })
      const disposition = (res.headers['content-disposition'] as string) || ''
      const match = disposition.match(/filename="?([^";]+)"?/)
      const stem =
        fallbackTitle
          .replace(/[^a-zA-Z0-9-_ ]/g, '')
          .trim()
          .replace(/ /g, '-')
          .toLowerCase() || 'book'
      const filename = match?.[1] || `${stem}.${format}`

      const url = URL.createObjectURL(res.data)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      throw toApiError(err)
    }
  },
}

// ---------------------------------------------------------------------------
// Books + chapters store
// ---------------------------------------------------------------------------

interface BookState {
  books: BookSummary[]
  currentBook: BookDetail | null
  /** Loaded chapter details (scenes) keyed by chapter id. */
  chapterDetails: Record<string, ChapterDetail>
  isLoading: boolean
  error: string | null

  fetchBooks: () => Promise<void>
  createBook: (data: BookCreateData) => Promise<{ id: string }>
  fetchBook: (bookId: string) => Promise<BookDetail>
  updateBook: (bookId: string, data: BookUpdateData) => Promise<void>
  deleteBook: (bookId: string) => Promise<void>

  createChapter: (bookId: string, data: ChapterCreateData) => Promise<void>
  fetchChapter: (chapterId: string) => Promise<ChapterDetail>
  updateChapter: (chapterId: string, data: ChapterUpdateData) => Promise<void>
  moveChapter: (bookId: string, chapterId: string, position: number) => Promise<void>
  deleteChapter: (bookId: string, chapterId: string) => Promise<void>

  generateScene: (
    chapterId: string,
    request: ChapterSceneRequest
  ) => Promise<ChapterSceneGenerateResponse>
  moveScene: (chapterId: string, sceneId: string, position: number) => Promise<void>
}

export const useBookStore = create<BookState>((set, get) => ({
  books: [],
  currentBook: null,
  chapterDetails: {},
  isLoading: false,
  error: null,

  fetchBooks: async () => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<BookListResponse>('/books/')
      set({ books: data.books, isLoading: false })
    } catch (err) {
      const apiError = toApiError(err)
      set({ isLoading: false, error: apiError.message })
      throw apiError
    }
  },

  createBook: async (payload) => {
    try {
      const { data } = await apiClient.post<{ id: string; title: string; status: string }>(
        '/books/',
        payload
      )
      await get().fetchBooks()
      return { id: data.id }
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchBook: async (bookId) => {
    set({ isLoading: true, error: null })
    try {
      const { data } = await apiClient.get<BookDetail>(`/books/${bookId}`)
      set({ currentBook: data, isLoading: false })
      return data
    } catch (err) {
      const apiError = toApiError(err)
      set({ isLoading: false, error: apiError.message })
      throw apiError
    }
  },

  updateBook: async (bookId, payload) => {
    try {
      await apiClient.patch(`/books/${bookId}`, payload)
      const current = get().currentBook
      if (current && current.id === bookId) {
        set({ currentBook: { ...current, ...payload } })
      }
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteBook: async (bookId) => {
    try {
      await apiClient.delete(`/books/${bookId}`)
      set((state) => ({
        books: state.books.filter((b) => b.id !== bookId),
        currentBook: state.currentBook?.id === bookId ? null : state.currentBook,
      }))
    } catch (err) {
      throw toApiError(err)
    }
  },

  createChapter: async (bookId, payload) => {
    try {
      await apiClient.post(`/books/${bookId}/chapters`, payload)
      await get().fetchBook(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchChapter: async (chapterId) => {
    try {
      const { data } = await apiClient.get<ChapterDetail>(`/books/chapters/${chapterId}`)
      set((state) => ({
        chapterDetails: { ...state.chapterDetails, [chapterId]: data },
      }))
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  updateChapter: async (chapterId, payload) => {
    try {
      const { data } = await apiClient.patch<ChapterDetail>(
        `/books/chapters/${chapterId}`,
        payload
      )
      set((state) => {
        const existing = state.chapterDetails[chapterId]
        return {
          chapterDetails: existing
            ? { ...state.chapterDetails, [chapterId]: { ...existing, ...data } }
            : state.chapterDetails,
          currentBook: state.currentBook
            ? {
                ...state.currentBook,
                chapters: state.currentBook.chapters.map((c) =>
                  c.id === chapterId ? { ...c, ...data } : c
                ),
              }
            : state.currentBook,
        }
      })
    } catch (err) {
      throw toApiError(err)
    }
  },

  moveChapter: async (bookId, chapterId, position) => {
    try {
      await apiClient.patch(`/books/chapters/${chapterId}/position`, { position })
      await get().fetchBook(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteChapter: async (bookId, chapterId) => {
    try {
      await apiClient.delete(`/books/chapters/${chapterId}`)
      set((state) => {
        const { [chapterId]: _removed, ...rest } = state.chapterDetails
        return { chapterDetails: rest }
      })
      await get().fetchBook(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  generateScene: async (chapterId, request) => {
    try {
      const { data } = await apiClient.post<ChapterSceneGenerateResponse>(
        `/books/chapters/${chapterId}/scenes/generate`,
        request
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  moveScene: async (chapterId, sceneId, position) => {
    try {
      await booksApi.moveScene(sceneId, position)
      await get().fetchChapter(chapterId)
    } catch (err) {
      throw toApiError(err)
    }
  },
}))

// ---------------------------------------------------------------------------
// Planning store (plans, threads, continuity — all scoped to one book)
// ---------------------------------------------------------------------------

interface PlanningState {
  plans: BookPlan[]
  threads: PlotThread[]
  reports: ContinuityReport[]

  fetchPlans: (bookId: string) => Promise<void>
  savePlan: (bookId: string, kind: PlanKind, content: PlanNode[]) => Promise<void>
  generatePlan: (
    bookId: string,
    kind: PlanKind,
    chaptersTarget: number
  ) => Promise<void>
  promoteNode: (bookId: string, nodeIndex: number) => Promise<PromoteNodeResponse>

  fetchThreads: (bookId: string) => Promise<void>
  createThread: (bookId: string, data: ThreadCreateData) => Promise<void>
  updateThread: (bookId: string, threadId: string, data: ThreadUpdateData) => Promise<void>
  deleteThread: (bookId: string, threadId: string) => Promise<void>
  addThreadEvent: (
    bookId: string,
    threadId: string,
    data: ThreadEventCreateData
  ) => Promise<void>

  fetchContinuityReports: (bookId: string) => Promise<ContinuityReport[]>
  runContinuityCheck: (bookId: string, chapterId?: string) => Promise<ContinuityStartResponse>
}

export const usePlanningStore = create<PlanningState>((set, get) => ({
  plans: [],
  threads: [],
  reports: [],

  fetchPlans: async (bookId) => {
    try {
      const { data } = await apiClient.get<BookPlanListResponse>(`/books/${bookId}/plans`)
      set({ plans: data.plans })
    } catch (err) {
      throw toApiError(err)
    }
  },

  savePlan: async (bookId, kind, content) => {
    try {
      await apiClient.put(`/books/${bookId}/plans`, { kind, content })
      await get().fetchPlans(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  generatePlan: async (bookId, kind, chaptersTarget) => {
    try {
      await apiClient.post(`/books/${bookId}/plans/generate`, {
        kind,
        chapters_target: chaptersTarget,
      })
      await get().fetchPlans(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  promoteNode: async (bookId, nodeIndex) => {
    try {
      const { data } = await apiClient.post<PromoteNodeResponse>(
        `/books/${bookId}/plans/promote`,
        { node_index: nodeIndex }
      )
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchThreads: async (bookId) => {
    try {
      const { data } = await apiClient.get<PlotThreadListResponse>(
        `/books/${bookId}/threads`
      )
      set({ threads: data.threads })
    } catch (err) {
      throw toApiError(err)
    }
  },

  createThread: async (bookId, payload) => {
    try {
      await apiClient.post(`/books/${bookId}/threads`, payload)
      await get().fetchThreads(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  updateThread: async (bookId, threadId, payload) => {
    try {
      await apiClient.patch(`/threads/${threadId}`, payload)
      await get().fetchThreads(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  deleteThread: async (bookId, threadId) => {
    try {
      await apiClient.delete(`/threads/${threadId}`)
      await get().fetchThreads(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  addThreadEvent: async (bookId, threadId, payload) => {
    try {
      await apiClient.post(`/threads/${threadId}/events`, payload)
      await get().fetchThreads(bookId)
    } catch (err) {
      throw toApiError(err)
    }
  },

  fetchContinuityReports: async (bookId) => {
    try {
      const { data } = await apiClient.get<ContinuityReportListResponse>(
        `/books/${bookId}/continuity`
      )
      set({ reports: data.reports })
      return data.reports
    } catch (err) {
      throw toApiError(err)
    }
  },

  runContinuityCheck: async (bookId, chapterId) => {
    try {
      const { data } = await apiClient.post<ContinuityStartResponse>(
        `/books/${bookId}/continuity`,
        { chapter_id: chapterId ?? null }
      )
      await get().fetchContinuityReports(bookId)
      return data
    } catch (err) {
      throw toApiError(err)
    }
  },
}))
