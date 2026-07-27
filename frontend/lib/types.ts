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
// Sources (app/api/sources.py) — was Manuscript; book-rooted now.
// ---------------------------------------------------------------------------

export type SourceStatus = 'pending' | 'processing' | 'completed' | 'failed'

export type SourceKind = 'upload' | 'paste'

/** The latest extraction run attached to a source — lets a review that was
 *  navigated away from be resumed rather than stranded (PR review #3). */
export interface SourceLatestExtraction {
  id: string
  status: 'pending' | 'ready' | 'failed' | 'committed' | string
}

/** Raw source item as returned by the API (list + detail). Book-scoped. */
export interface ApiSource {
  id: string
  book_id: string
  kind?: SourceKind | string
  title: string
  author?: string | null
  word_count: number
  status: SourceStatus
  uploaded_at: string | null
  processed_at?: string | null
  /** The list and detail endpoints both return the latest extraction run. */
  latest_extraction?: SourceLatestExtraction | null
  /** The stored text — returned by GET /sources/{id}; lets a paste source be
   *  viewed and edited in place. */
  content_text?: string | null
}

/** POST /sources/paste and PATCH /sources/{id} response. `extraction_run_id` is
 *  null when an edit changed only the title (no re-extraction). */
export interface SourceMutateResponse {
  id: string
  book_id: string
  title: string
  word_count: number
  status: SourceStatus
  extraction_run_id: string | null
  message: string
}

/**
 * Source as consumed by the UI: the raw API fields plus normalized
 * aliases (`created_at` <- uploaded_at, `processing_status` <- status).
 */
export interface Source extends ApiSource {
  created_at: string
  processing_status: SourceStatus
  /** Not returned by the list/detail endpoints today; populated when known. */
  character_count?: number
}

/** GET /sources/ */
export interface SourceListResponse {
  sources: ApiSource[]
  total: number
  skip: number
  limit: number
}

/** POST /sources/upload — auto-creates a book when no book_id is given, and
 *  starts an extraction run whose proposals the author reviews before commit. */
export interface SourceUploadResponse {
  id: string
  book_id: string
  title: string
  author?: string | null
  word_count: number
  status: SourceStatus
  /** Handle to the extraction whose proposals must be reviewed + committed. */
  extraction_run_id: string
  message: string
}

// ---------------------------------------------------------------------------
// Characters (GET /sources/{id}/characters). A character belongs to one book.
// ---------------------------------------------------------------------------

export interface Character {
  id: string
  name: string
  description?: string | null
  role?: string | null
  book_id?: string | null
  /** Provenance: the source this character was extracted from, if any. */
  source_id?: string | null
  dialogue_count?: number
  indexed_at?: string | null
  /** Optional enrichments some views render when present. */
  traits?: string[]
  total_chunks?: number
}

export interface SourceCharactersResponse {
  source_id: string
  characters: Character[]
}

// ---------------------------------------------------------------------------
// Scenes (app/api/scenes.py)
// ---------------------------------------------------------------------------

/** POST /scenes/generate body (app/core/models.py SceneRequest). */
export interface SceneRequest {
  source_id: string
  characters: string[]
  scene_description: string
  setting: string
  emotional_tone: string
  pov_character?: string
  /** 100–3000; backend default 500. */
  target_word_count: number
  style_notes?: string
}

export type SceneStatus =
  | 'processing'
  | 'paused'
  | 'completed'
  | 'failed'
  | string

/**
 * Scene item. The list endpoint returns a summary (`preview`, no `content`);
 * the detail endpoint fills `content`, `scene_request`, `word_count`,
 * `evaluation_scores`.
 */
export interface Scene {
  id: string
  source_id: string | null
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

/** A scene as listed by GET /books/{id}/scenes — every scene the book owns,
 *  whether filed in a chapter or standalone (source-based). */
export interface BookSceneSummary {
  id: string
  title: string | null
  status: SceneStatus
  chapter_id: string | null
  chapter_title?: string | null
  characters?: string[]
  preview?: string | null
  created_at: string | null
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
  source_id?: string
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
// Canon entities (app/api/canon.py) — worldbuilding + style, per book.
// ---------------------------------------------------------------------------

export type CanonCategory =
  | 'world'
  | 'location'
  | 'faction'
  | 'item'
  | 'concept'
  | 'org'

/** A categorized worldbuilding fact. */
export interface CanonEntry {
  id: string
  name: string
  category: CanonCategory | string
  content?: string | null
  position: number
}

/** A book's prose style guide (one per book). */
export interface StyleGuide {
  id: string
  pov?: string | null
  tense?: string | null
  tone?: string | null
  comps?: string | null
  sample_prose?: string | null
}

/** GET /books/{id}/canon */
export interface CanonResponse {
  entries: CanonEntry[]
  style: StyleGuide | null
}

// ---------------------------------------------------------------------------
// Extraction (app/api/extraction.py) — Source -> proposed Canon -> commit
// ---------------------------------------------------------------------------

export interface CharacterProposal {
  name: string
  role?: string | null
  description?: string | null
}

export interface CanonEntryProposal {
  name: string
  category: CanonCategory | string
  content?: string | null
}

export interface ExtractionProposals {
  characters: CharacterProposal[]
  canon_entries: CanonEntryProposal[]
  style: Partial<StyleGuide>
  synopsis: string
}

// ---------------------------------------------------------------------------
// Voice-chunk browser + retrieval inspector (app/api/characters.py, Phase 7)
// ---------------------------------------------------------------------------

/** A character's indexed voice chunk (GET /characters/{id}/chunks). */
export interface VoiceChunk {
  id: string
  chunk_type: string
  text: string
  source: string
  word_count: number
}

/** A scored retrieval hit (POST /characters/{id}/retrieve). */
export interface RetrievalHit {
  text: string
  score: number | null
  chunk_type: string
  source: string
  word_count: number
}

/** GET /books/{id}/extractions/{run_id} */
export interface ExtractionRun {
  id: string
  source_id: string | null
  status: 'pending' | 'ready' | 'failed' | 'committed' | string
  proposals: ExtractionProposals | Record<string, never>
  error?: string | null
}

/** POST /books/{id}/extractions/{run_id}/commit body — the reviewed selection. */
export interface ExtractionCommit {
  characters?: CharacterProposal[]
  canon_entries?: CanonEntryProposal[]
  style?: Partial<StyleGuide>
  synopsis?: string
}

/** A skipped item the commit did NOT apply (e.g. a blank name). The UI must
 *  surface these rather than redirect as if everything succeeded (PR review #1). */
export interface ExtractionSkipped {
  type: string
  name: string
  reason: string
}

/** Per-type commit outcome. Approved existing items are MERGED (updated), never
 *  silently dropped; only `skipped` items were not applied (PR review #1). */
export interface ExtractionCommitResult {
  characters: { created: string[]; updated: string[] }
  canon_entries: { created: string[]; updated: string[] }
  style: 'created' | 'updated' | null
  synopsis: 'created' | 'updated' | null
  skipped: ExtractionSkipped[]
}

/** POST /books/{id}/extractions/{run_id}/commit response. */
export interface ExtractionCommitResponse {
  run_id: string
  status: string
  result: ExtractionCommitResult
}

// ---------------------------------------------------------------------------
// Versioning (app/api/versioning.py)
// ---------------------------------------------------------------------------

export type VersionedEntityType =
  | 'book_plan'
  | 'synopsis'
  | 'character'
  | 'canon_entry'
  | 'style_guide'

/** One entry in an entity's version history (newest first; max == live). */
export interface EntityVersionSummary {
  version_no: number
  reason: string | null
  created_at: string | null
  created_by?: string | null
}

/** GET /books/{id}/versions/{type}/{entity_id} */
export interface EntityVersionListResponse {
  entity_type: VersionedEntityType | string
  entity_id: string
  versions: EntityVersionSummary[]
}

/** GET /books/{id}/versions/{type}/{entity_id}/{version_no} — full snapshot. */
export interface EntityVersionDetail {
  version_no: number
  reason: string | null
  content: Record<string, unknown>
  created_at: string | null
}

/** Full character profile (GET /characters/{id}). */
export interface CharacterDetail {
  id: string
  book_id: string
  source_id?: string | null
  name: string
  description?: string | null
  role?: string | null
  goals?: string | null
  arc?: string | null
  notes?: string | null
  personality_traits?: Record<string, unknown>
  voice_characteristics?: Record<string, unknown>
  relationships?: Record<string, unknown>
  dialogue_count?: number
  indexed_at?: string | null
}

/** PATCH /characters/{id} body — the editable profile fields. The three dict
 *  fields are edited via a key-value editor. */
export interface CharacterUpdateData {
  name?: string
  role?: string | null
  description?: string | null
  goals?: string | null
  arc?: string | null
  notes?: string | null
  personality_traits?: Record<string, string>
  voice_characteristics?: Record<string, string>
  relationships?: Record<string, string>
}

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
export type PlanStatus = 'ready' | 'generating' | 'pending' | 'failed' | string

export interface BookPlan {
  id: string
  book_id: string
  kind: PlanKind | string
  content: PlanNode[]
  /** Staged outline runs as a background job; poll until ready/failed. */
  status?: PlanStatus
  /** Current stage of an in-flight staged outline (skeleton/chapters/beats…). */
  stage?: string | null
  /** Non-fatal fidelity warnings attached to a generated plan. */
  warnings?: string[]
  /** Failure reason when status === 'failed'. */
  error?: string | null
  updated_at: string | null
}

/** POST /books/{id}/plans/generate — async (staged) returns status:'generating'
 *  with a job handle; the sync path returns the finished plan. */
export interface PlanGenerateResponse {
  plan_id?: string
  job_id?: string
  status?: PlanStatus
  content?: PlanNode[]
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
