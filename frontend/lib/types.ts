/**
 * Shared TypeScript types for the Polyphony frontend.
 *
 * Shapes mirror the FastAPI backend at /api/v1 (see app/api/*.py).
 * Where the UI reads differently-named fields than the API returns
 * (created_at vs uploaded_at, processing_status vs status), the raw
 * API shape is kept alongside normalized fields; normalization happens
 * in lib/api-client.ts.
 */

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Normalized error shape thrown by the API client and stores. */
export interface ApiError {
  /** Human-readable message (backend `detail` when available). */
  message: string
  /** HTTP status code, when the request reached the server. */
  status?: number
  /** Raw backend `detail` payload (string or validation error array). */
  detail?: unknown
}

// ---------------------------------------------------------------------------
// Auth (app/api/auth.py)
// ---------------------------------------------------------------------------

/** GET /auth/me */
export interface User {
  id: string
  email: string
  full_name?: string | null
  role?: string
  created_at?: string | null
}

/** POST /auth/login and /auth/refresh; /auth/register additionally returns `user`. */
export interface AuthTokenResponse {
  access_token: string
  token_type: string
  user?: Pick<User, 'id' | 'email' | 'full_name'>
}

/** OAuth2 password form credentials (username = email). */
export interface LoginCredentials {
  username: string
  password: string
}

/** POST /auth/register body. Registration is invite-gated. */
export interface RegisterData {
  email: string
  password: string
  full_name?: string
  invite_code: string
}

// ---------------------------------------------------------------------------
// Manuscripts (app/api/manuscripts.py)
// ---------------------------------------------------------------------------

export type ManuscriptStatus = 'pending' | 'processing' | 'completed' | 'failed'

/** Raw manuscript item as returned by the API (list + detail). */
export interface ApiManuscript {
  id: string
  title: string
  author?: string | null
  word_count: number
  status: ManuscriptStatus
  uploaded_at: string | null
  processed_at?: string | null
}

/**
 * Manuscript as consumed by the UI: the raw API fields plus normalized
 * aliases (`created_at` <- uploaded_at, `processing_status` <- status).
 */
export interface Manuscript extends ApiManuscript {
  created_at: string
  processing_status: ManuscriptStatus
  /** Not returned by the list/detail endpoints today; populated when known. */
  character_count?: number
}

/** GET /manuscripts/ */
export interface ManuscriptListResponse {
  manuscripts: ApiManuscript[]
  total: number
  skip: number
  limit: number
}

/** POST /manuscripts/upload */
export interface ManuscriptUploadResponse {
  id: string
  title: string
  author?: string | null
  word_count: number
  status: ManuscriptStatus
  message: string
}

// ---------------------------------------------------------------------------
// Characters (GET /manuscripts/{id}/characters)
// ---------------------------------------------------------------------------

export interface Character {
  id: string
  name: string
  description?: string | null
  role?: string | null
  manuscript_id?: string | null
  book_id?: string | null
  dialogue_count?: number
  indexed_at?: string | null
  /** Optional enrichments some views render when present. */
  traits?: string[]
  total_chunks?: number
}

export interface ManuscriptCharactersResponse {
  manuscript_id: string
  characters: Character[]
}

// ---------------------------------------------------------------------------
// Scenes (app/api/scenes.py)
// ---------------------------------------------------------------------------

/** POST /scenes/generate body (app/core/models.py SceneRequest). */
export interface SceneRequest {
  manuscript_id: string
  characters: string[]
  scene_description: string
  setting: string
  emotional_tone: string
  pov_character?: string
  /** 100–3000; backend default 500. */
  target_word_count: number
  style_notes?: string
}

export type SceneStatus = 'processing' | 'completed' | 'failed' | string

/**
 * Scene item. The list endpoint returns a summary (`preview`, no `content`);
 * the detail endpoint fills `content`, `scene_request`, `word_count`,
 * `evaluation_scores`.
 */
export interface Scene {
  id: string
  manuscript_id: string | null
  characters: string[]
  status: SceneStatus
  created_at: string | null
  generation_time_ms?: number | null
  // list (summary) only
  preview?: string | null
  // detail only
  content?: string | null
  scene_request?: SceneRequest | Record<string, unknown>
  word_count?: number | null
  evaluation_scores?: Record<string, number> | null
}

/** GET /scenes/ */
export interface SceneListResponse {
  scenes: Scene[]
  total: number
  skip?: number
  limit?: number
}

/** POST /scenes/generate — generation runs in the background; poll GET /scenes/{scene_id}. */
export interface SceneGenerateResponse {
  scene_id: string
  status: SceneStatus
  message: string
}

// ---------------------------------------------------------------------------
// Books (app/api/books.py)
// ---------------------------------------------------------------------------

export type BookStatus = 'drafting' | 'revising' | 'complete'

/** Item in GET /books/ */
export interface BookSummary {
  id: string
  title: string
  author?: string | null
  genre?: string | null
  status: BookStatus | string
  created_at: string | null
}

/** GET /books/ */
export interface BookListResponse {
  books: BookSummary[]
}

/** POST /books/ body */
export interface BookCreateData {
  title: string
  author?: string
  synopsis?: string
  genre?: string
}

/** PATCH /books/{id} body */
export interface BookUpdateData {
  title?: string
  author?: string
  synopsis?: string
  genre?: string
  status?: BookStatus
}

/** Chapter as returned inside GET /books/{id} and by the chapter endpoints. */
export interface BookChapter {
  id: string
  book_id: string
  position: number
  title: string
  summary?: string | null
  status?: string | null
}

/** GET /books/{id} */
export interface BookDetail {
  id: string
  title: string
  author?: string | null
  synopsis?: string | null
  genre?: string | null
  status: BookStatus | string
  chapters: BookChapter[]
}

/** POST /books/{id}/chapters body */
export interface ChapterCreateData {
  title: string
  summary?: string
  position?: number
}

/** PATCH /books/chapters/{id} body */
export interface ChapterUpdateData {
  title?: string
  summary?: string
  status?: string
}

/** Scene summary inside GET /books/chapters/{id}. */
export interface ChapterScene {
  id: string
  position: number
  status: SceneStatus
  word_count?: number | null
  preview?: string | null
  created_at: string | null
}

/** GET /books/chapters/{id} — the chapter plus its ordered scenes. */
export interface ChapterDetail extends BookChapter {
  scenes: ChapterScene[]
}

/** POST /books/chapters/{id}/scenes/generate body. */
export interface ChapterSceneRequest {
  manuscript_id?: string
  characters: string[]
  scene_description: string
  setting: string
  emotional_tone: string
  pov_character?: string
  /** 100–3000; backend default 800. */
  target_word_count: number
  style_notes?: string
}

/** POST /books/chapters/{id}/scenes/generate response — poll GET /scenes/{scene_id}. */
export interface ChapterSceneGenerateResponse {
  scene_id: string
  chapter_id: string
  position: number
  status: SceneStatus
}

/** PUT /books/scenes/{id}/content response. */
export interface SceneContentSaveResponse {
  id: string
  word_count: number
  updated_at: string
}

/** Revision item in GET /books/scenes/{id}/revisions. */
export interface SceneRevision {
  id: string
  word_count: number | null
  source: string
  created_at: string | null
  content: string
}

/** GET /books/scenes/{id}/revisions */
export interface SceneRevisionsResponse {
  scene_id: string
  revisions: SceneRevision[]
}

/** Book export formats (GET /books/{id}/export?format=...). */
export type BookExportFormat = 'md' | 'docx' | 'epub'

// ---------------------------------------------------------------------------
// Plans, threads, continuity (app/api/plans.py)
// ---------------------------------------------------------------------------

export type PlanKind = 'outline' | 'beat_sheet'

/** Outline node: {title, summary, children[]} (app/planning/outline.py). */
export interface PlanNode {
  title: string
  summary?: string
  children?: PlanNode[]
}

/** Plan as returned by GET/PUT /books/{id}/plans. */
export interface BookPlan {
  id: string
  book_id: string
  kind: PlanKind | string
  content: PlanNode[]
  updated_at: string | null
}

/** GET /books/{id}/plans */
export interface BookPlanListResponse {
  plans: BookPlan[]
}

/** POST /books/{id}/plans/promote response. */
export interface PromoteNodeResponse {
  chapter_id: string
  title: string
  position: number
}

export type ThreadStatus = 'open' | 'resolved' | 'abandoned'

export type ThreadEventKind = 'setup' | 'development' | 'payoff'

/** Event inside a plot thread. */
export interface PlotThreadEvent {
  id: string
  note: string
  kind: ThreadEventKind | string
  scene_id: string | null
  chapter_id: string | null
  position: number
}

/** Plot thread (events present on the list endpoint). */
export interface PlotThread {
  id: string
  book_id: string
  name: string
  description?: string | null
  status: ThreadStatus | string
  color?: string | null
  events?: PlotThreadEvent[]
}

/** GET /books/{id}/threads */
export interface PlotThreadListResponse {
  threads: PlotThread[]
}

/** POST /books/{id}/threads body */
export interface ThreadCreateData {
  name: string
  description?: string
  color?: string
}

/** PATCH /threads/{id} body */
export interface ThreadUpdateData {
  name?: string
  description?: string
  status?: ThreadStatus
  color?: string
}

/** POST /threads/{id}/events body */
export interface ThreadEventCreateData {
  note: string
  kind: ThreadEventKind
  scene_id?: string
  chapter_id?: string
}

export type ContinuityFindingSeverity = 'critical' | 'major' | 'minor'

/** One continuity finding (app/planning/continuity.py validate_findings). */
export interface ContinuityFinding {
  type: 'timeline' | 'character' | 'object' | 'thread' | 'other' | string
  severity: ContinuityFindingSeverity | string
  detail: string
  refs?: string
}

/** Report item in GET /books/{id}/continuity. */
export interface ContinuityReport {
  id: string
  scope: 'book' | 'chapter' | string
  chapter_id: string | null
  status: 'processing' | 'completed' | 'failed' | string
  findings: ContinuityFinding[] | null
  model?: string | null
  tokens_used?: number | null
  created_at: string | null
}

/** GET /books/{id}/continuity */
export interface ContinuityReportListResponse {
  reports: ContinuityReport[]
}

/** POST /books/{id}/continuity response. */
export interface ContinuityStartResponse {
  report_id: string
  status: string
}
