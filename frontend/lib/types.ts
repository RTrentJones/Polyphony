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
  job?: JobSummary | null
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
  /** Handle to the extraction whose proposals must be reviewed + committed;
   *  null when the preflight refused (see `preflight`). */
  extraction_run_id: string | null
  preflight?: ExtractionPreflight | null
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

/** A scene's stored generation request (GET /scenes/{id} `scene_request`).
 *  Read-only: requests are sent as ChapterSceneRequest (cast by `character_ids`);
 *  `characters` here is the cast's names, derived server-side for display. */
export interface SceneRequest {
  source_id: string
  characters: string[]
  character_ids?: string[]
  scene_description: string
  setting: string
  emotional_tone: string
  pov_character?: string
  /** 100–3000; backend default 500. */
  target_word_count: number
  style_notes?: string
  /** The planned beat this scene was written from (SPEC-23). */
  planned_beat_id?: string
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
  job?: JobSummary | null
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
  /** Progress (SPEC-27): words in live scenes, chapters with a drafted scene,
   *  completed scenes out of the chapters' planned beats. Absent on an old API. */
  word_count?: number
  chapter_count?: number
  chapters_drafted?: number
  scenes_drafted?: number
  scenes_planned?: number
  /** The most recently edited live scene: the dashboard's "Resume" link. */
  last_edited_scene?: LastEditedScene | null
}

/** `BookSummary.last_edited_scene` (SPEC-27). */
export interface LastEditedScene {
  id: string
  title?: string | null
  chapter_id?: string | null
  chapter_title?: string | null
  updated_at?: string | null
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
  /** Promoted from an outline node (SPEC-10): the node's stable id and its
   *  structural cast, which a new scene in this chapter defaults to. */
  plan_node_id?: string | null
  pov_character_id?: string | null
  character_ids?: string[]
  /** The chapter's planned scenes as data (SPEC-23). */
  planned_beats?: PlannedBeat[]
}

/** One planned scene of a chapter (SPEC-23). */
export interface PlannedBeat {
  node_id: string
  title: string
  summary: string
  /** [] = the chapter's cast. */
  character_ids: string[]
  secondary_names: string[]
  setting: string
  tone: string
  /** The live cast the server writes this beat with (SPEC-32). */
  readonly cast_ids: string[]
}

/** A person the outline names who is in the synopsis/canon but not the cast (SPEC-24). */
export interface SuggestedCharacter {
  name: string
  synopsis_sentence: string
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
  character_count: number // live characters (SPEC-32)
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
  job?: JobSummary | null
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
  /** The cast, as character ids of this book (SPEC-01). */
  character_ids: string[]
  /** Generation mode: prose (default) or the ensemble loop (tier-gated). */
  mode?: 'prose' | 'ensemble'
  scene_description: string
  setting: string
  emotional_tone: string
  pov_character?: string
  /** 100–3000; backend default 800. */
  target_word_count: number
  style_notes?: string
  /** The planned beat this scene is written from (SPEC-23): links the scene
   *  to the beat, so generate-all-planned skips it. */
  planned_beat_id?: string
}

/** POST /books/chapters/{id}/scenes/generate response — poll GET /scenes/{scene_id}. */
export interface ChapterSceneGenerateResponse {
  scene_id: string
  job_id?: string
  chapter_id: string
  position: number
  status: SceneStatus
  mode_requested?: 'prose' | 'ensemble'
  mode_used?: 'prose' | 'ensemble'
  /** Why the mode that ran differs from the one asked for (SPEC-12). */
  mode_reason?: string | null
}

/** POST /books/chapters/{id}/scenes/generate-planned request (SPEC-10). The
 *  scene descriptions are the chapter's planned beats; the cast defaults to
 *  the chapter's promoted one. */
export interface PlannedScenesRequest {
  character_ids?: string[]
  setting?: string
  /** Defaults to '' server-side (no injected "neutral" tone). */
  emotional_tone?: string
  target_word_count?: number
  style_notes?: string | null
  mode?: 'prose' | 'ensemble'
}

/** Its response: the first scene of the chain, or the batch already running. */
export interface PlannedScenesResponse {
  chapter_id: string
  scene_id: string | null
  job_id: string | null
  status: string
  /** Planned beats in the chapter. */
  total: number | null
  /** Scenes that will be written, one after another. */
  queued: number
  /** Beats that already had a scene. */
  skipped?: number
  already_running?: boolean
  mode_requested?: 'prose' | 'ensemble'
  mode_used?: 'prose' | 'ensemble'
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

/** What the export dialog asks for (B9). Chapter numbers are 1-based, inclusive. */
export interface BookExportOptions {
  format: BookExportFormat
  /** Put the synopsis at the front (default off: it is the storyboard, not prose). */
  includeSynopsis: boolean
  /** Leave out chapters with no written scene (default on). */
  skipEmpty: boolean
  /** First chapter to export by book position; null/undefined = from the start. */
  fromChapter?: number | null
  /** Last chapter to export by book position; null/undefined = to the end. */
  toChapter?: number | null
}

/**
 * The query string of GET /books/{id}/export (B9, agreed with the backend):
 * `?format=&include_synopsis=&skip_empty=&from_chapter=&to_chapter=`. The range
 * keys are omitted when unset; the server answers 422 when from > to.
 */
export interface BookExportQuery {
  format: BookExportFormat
  include_synopsis: boolean
  skip_empty: boolean
  from_chapter?: number
  to_chapter?: number
}

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
  // The full bible entry extraction proposes (SPEC-09).
  goals?: string | null
  arc?: string | null
  relationships?: Record<string, string> | null
  personality_traits?: Record<string, string> | null
  voice_characteristics?: Record<string, string> | null
  aliases?: string[] | null
  /** Ranking: name occurrences in the source, and windows it was seen in. */
  mentions?: number
  windows?: number
  /** The review's default (seen in 2+ windows, or a narrator). */
  selected?: boolean
}

/** What an extraction will cost, returned by every enqueueing endpoint. */
export interface ExtractionPreflight {
  windows: number
  map_calls: number
  reduce_calls: number
  requests: number
  estimated_tokens: number
  capacity_tokens: number
  fits: boolean
  /** When refused: the largest opening (in chapters) that fits, if any. */
  max_chapters: number | null
  message?: string
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
  stats?: { windows: number; calls?: number; tokens?: number; max_chapters?: number }
  warnings?: string[]
}

// ---------------------------------------------------------------------------
// Voice-chunk browser + retrieval inspector (app/api/characters.py, Phase 7)
// ---------------------------------------------------------------------------

/** A character's indexed voice chunk (GET /characters/{id}/chunks). */
export interface VoiceChunk {
  id: string
  source_id?: string | null
  chunk_type: string
  text: string
  source: string
  word_count: number
  created_at?: string | null
}

/** A scored retrieval hit (POST /characters/{id}/retrieve). */
export interface RetrievalHit {
  id?: string | null
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
  /** Map-reduce progress while pending (SPEC-09). */
  progress?: {
    phase?: string | null
    windows_total?: number | null
    windows_done?: number | null
    reduce_total?: number | null
    reduce_done?: number | null
  }
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
  | 'source'
  | 'chapter'

/** POST .../restore/{n}. A source whose text changed offers re-extraction
 *  (SPEC-13) — it is never run automatically. */
export interface RestoreVersionResult {
  entity_type: string
  entity_id: string
  restored_from: number
  reextract?: { offered: boolean; endpoint: string; message: string }
}

// ---------------------------------------------------------------------------
// Trash (app/api/trash.py, SPEC-13)
// ---------------------------------------------------------------------------

export type TrashEntityType = 'character' | 'canon_entry' | 'source' | 'chapter' | 'scene'

/** One soft-deleted entity of a book, with what restoring it brings back. */
export interface TrashItem {
  type: TrashEntityType
  id: string
  name: string
  deleted_at: string
  restores: { voice_samples?: number; scenes?: number; words?: number }
  summary: string
}

/** 409 body of a restore that would collide: the live entity in the way. */
export interface TrashConflictDetail {
  message: string
  conflict: { type: string; id: string; name: string } | null
}

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
  /** Stable across edits; promote is idempotent on it (SPEC-10). */
  node_id?: string
  pov?: string | null
  character_ids?: string[]
  act_index?: number | null
  threads?: string[]
  /** Named people in the synopsis/canon but not the cast (SPEC-24, §1.1). */
  secondary_names?: string[]
  /** A beat child's place and mood (SPEC-24, §1.1); omitted when the model gave none. */
  setting?: string
  tone?: string
}

/** The skeleton the model understood, shown for confirmation before the
 *  chapters stage builds on it (SPEC-10). */
export interface PlanPremise {
  premise_restated?: string
  central_conflict?: string
  acts?: unknown[]
  confirmed?: boolean
  edited?: boolean
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
  premise?: PlanPremise | null
  updated_at: string | null
  job?: JobSummary | null
  /** Secondary people the outline names who are not in the cast (SPEC-24). */
  suggested_characters?: SuggestedCharacter[]
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
  plan_node_id?: string | null
  /** False when the node already had a chapter (promote is idempotent). */
  created?: boolean
  suggested_characters?: SuggestedCharacter[]
}

export interface PromoteAllResponse {
  chapters: PromoteNodeResponse[]
  created: number
  suggested_characters?: SuggestedCharacter[]
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

/** One continuity finding (app/planning/continuity.py Finding, SPEC-31). */
export interface ContinuityFinding {
  type: 'timeline' | 'character' | 'object' | 'thread' | 'other' | string
  severity: ContinuityFindingSeverity | string
  detail: string
  /** The model's quote and the scene the server found it in; absent on pre-SPEC-31 reports. */
  quote?: string
  scene_id?: string | null
}

/** Report item in GET /books/{id}/continuity. */
export interface ContinuityReport {
  id: string
  scope: 'book' | 'chapter' | string
  chapter_id: string | null
  status: 'processing' | 'paused' | 'completed' | 'incomplete' | 'failed' | string
  findings: ContinuityFinding[] | null
  /** Which sections were actually checked (SPEC-02). */
  coverage?: { chunks_checked: number; chunks_total: number; unchecked: string[] } | null
  model?: string | null
  tokens_used?: number | null
  created_at: string | null
  job?: JobSummary | null
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

// ---------------------------------------------------------------------------
// Jobs (app/api/jobs.py, SPEC-07/08)
// ---------------------------------------------------------------------------

/** One lifecycle for every LLM job. 'paused' = waiting for AI quota. */
export type JobStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

/** Stage progress from the job's checkpoint ("beat 3 of 5"). */
export interface JobProgress {
  stage?: string
  done: number
  total: number
}

/** GET /jobs/{id} */
export interface Job {
  id: string
  kind: string
  status: JobStatus
  available_at: string | null
  /** When a paused job is due to resume. */
  resume_at: string | null
  attempts: number
  max_attempts: number
  pause_count: number
  cancel_requested: boolean
  cancellable: boolean
  error: string | null
  progress: JobProgress | null
  domain: { type: string; id: string } | null
  created_at: string | null
  finished_at: string | null
}

/** A domain row's newest job, embedded in list payloads. */
export interface JobSummary {
  job_id: string
  status: JobStatus
  resume_at: string | null
  progress: JobProgress | null
}
