"""Book-as-root baseline (frozen explicit DDL).

Squashes the old six-migration chain (0001 create_all + 0002–0006) into a single
explicit-DDL baseline carrying the book-as-root target shape
(docs/ADR-002-book-as-root.md §8). Polyphony's live data is disposable, so there
is nothing to migrate — a fresh Postgres gets exactly this schema.

Why explicit DDL, not `create_all`:
    The old baseline called `Base.metadata.create_all`, which reads the *live*
    ORM at run time. A fresh DB therefore got whatever shape the ORM currently
    had, and any additive migration then ran against already-correct state — the
    drift landmine that already bit 0006 ("make constraint adds idempotent —
    fresh-DB baseline overlap"). Freezing the DDL here means this revision builds
    exactly the tables that existed when it was written and nothing more, so the
    NEXT migration (e.g. Phase 2 `entity_versions`) has a stable, known baseline
    to add onto — no overlap, no guards.

    The statements below were generated from the ORM via the postgresql dialect
    compiler, so they match `create_all` exactly at squash time — but they will
    not silently absorb tables added to the ORM later.

FK ordering: tables are emitted in `Base.metadata.sorted_tables` order, a valid
creation order (no in-DB user↔invite_codes cycle — `users.invite_code_id` has no
DB-level FK).

`voice_chunks` stays OFF the ORM Base (a pgvector column sqlite can't build) and
is created here, postgres-only, now with `book_id NOT NULL` + a
`(book_id, character_id)` index (book is the root; docs/BRD.md R3).

Revision ID: 0001
Revises:
Create Date: 2026-07-22

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# One statement per entry: migrations run over asyncpg, whose extended query
# protocol rejects multi-statement executes.
TABLES: list[str] = [
    """
    CREATE TABLE users (
        id UUID NOT NULL,
        email VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL,
        full_name VARCHAR(255),
        is_active BOOLEAN DEFAULT 'true' NOT NULL,
        role VARCHAR(20) DEFAULT 'writer' NOT NULL,
        invite_code_id UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id)
    )
    """,
    "CREATE UNIQUE INDEX ix_users_email ON users (email)",
    """
    CREATE TABLE invite_codes (
        id UUID NOT NULL,
        code VARCHAR(64) NOT NULL,
        created_by UUID,
        max_uses INTEGER NOT NULL,
        uses INTEGER NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(created_by) REFERENCES users (id)
    )
    """,
    "CREATE UNIQUE INDEX ix_invite_codes_code ON invite_codes (code)",
    """
    CREATE TABLE refresh_tokens (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        token_hash VARCHAR(64) NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        user_agent VARCHAR(500),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_refresh_tokens_user_id ON refresh_tokens (user_id)",
    "CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens (token_hash)",
    """
    CREATE TABLE api_usage (
        id UUID NOT NULL,
        user_id UUID,
        endpoint VARCHAR(255),
        tokens_used INTEGER,
        cost_usd DECIMAL(10, 6),
        timestamp TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id)
    )
    """,
    "CREATE INDEX idx_api_usage_user_time ON api_usage (user_id, timestamp)",
    """
    CREATE TABLE jobs (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        kind VARCHAR(50) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(20) NOT NULL,
        attempts INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        locked_at TIMESTAMP WITH TIME ZONE,
        locked_by VARCHAR(100),
        available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        error TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_jobs_kind ON jobs (kind)",
    "CREATE INDEX idx_jobs_status_available ON jobs (status, available_at)",
    "CREATE INDEX idx_jobs_user_id ON jobs (user_id)",
    """
    CREATE TABLE books (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        title VARCHAR(500) NOT NULL,
        author VARCHAR(255),
        synopsis TEXT,
        genre VARCHAR(100),
        status VARCHAR(50) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_books_user_id ON books (user_id)",
    """
    CREATE TABLE book_plans (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        kind VARCHAR(20) NOT NULL,
        content JSON NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_book_plans_book_id ON book_plans (book_id)",
    """
    CREATE TABLE chapters (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        position INTEGER NOT NULL,
        title VARCHAR(500) NOT NULL,
        summary TEXT,
        status VARCHAR(50) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        CONSTRAINT uq_chapters_book_position UNIQUE (book_id, position),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_chapters_book_id ON chapters (book_id)",
    """
    CREATE TABLE plot_threads (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        status VARCHAR(20) NOT NULL,
        color VARCHAR(20),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_plot_threads_book_id ON plot_threads (book_id)",
    """
    CREATE TABLE sources (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        user_id UUID NOT NULL,
        kind VARCHAR(20) DEFAULT 'upload' NOT NULL,
        title VARCHAR(500) NOT NULL,
        author VARCHAR(255),
        content_hash VARCHAR(64),
        content_text TEXT,
        file_path VARCHAR(1000),
        word_count INTEGER,
        uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        processed_at TIMESTAMP WITH TIME ZONE,
        status VARCHAR(50),
        PRIMARY KEY (id),
        CONSTRAINT uq_sources_book_content UNIQUE (book_id, content_hash),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_sources_status ON sources (status)",
    "CREATE INDEX idx_sources_user_id ON sources (user_id)",
    "CREATE INDEX idx_sources_book_id ON sources (book_id)",
    """
    CREATE TABLE characters (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        user_id UUID NOT NULL,
        source_id UUID,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        personality_traits JSON,
        voice_characteristics JSON,
        role VARCHAR(100),
        goals TEXT,
        arc TEXT,
        relationships JSON,
        notes TEXT,
        dialogue_count INTEGER,
        indexed_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT uq_characters_book_name UNIQUE (book_id, name),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX idx_characters_source_id ON characters (source_id)",
    "CREATE INDEX idx_characters_book_id ON characters (book_id)",
    "CREATE INDEX idx_characters_user_id ON characters (user_id)",
    """
    CREATE TABLE continuity_reports (
        id UUID NOT NULL,
        book_id UUID NOT NULL,
        chapter_id UUID,
        scope VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL,
        findings JSON,
        model VARCHAR(100),
        tokens_used INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(book_id) REFERENCES books (id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_continuity_reports_book_id ON continuity_reports (book_id)",
    """
    CREATE TABLE scenes (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        source_id UUID,
        chapter_id UUID,
        position INTEGER NOT NULL,
        title VARCHAR(500),
        setting TEXT,
        emotional_tone VARCHAR(100),
        characters JSON,
        scene_description TEXT,
        scene_request JSON,
        generated_content TEXT,
        content TEXT,
        word_count INTEGER,
        status VARCHAR(50),
        generation_time_ms INTEGER,
        evaluation_scores JSON,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        CONSTRAINT uq_scenes_chapter_position UNIQUE (chapter_id, position),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE SET NULL,
        FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX idx_scenes_user_id ON scenes (user_id)",
    "CREATE INDEX idx_scenes_source_id ON scenes (source_id)",
    "CREATE INDEX idx_scenes_chapter_id ON scenes (chapter_id)",
    "CREATE INDEX idx_scenes_status ON scenes (status)",
    "CREATE INDEX idx_scenes_created_at ON scenes (created_at DESC)",
    """
    CREATE TABLE character_chunks (
        id UUID NOT NULL,
        character_id UUID NOT NULL,
        chunk_type VARCHAR(50),
        content TEXT NOT NULL,
        source_location VARCHAR(500),
        embedding_id VARCHAR(255),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(character_id) REFERENCES characters (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_character_chunks_character_id ON character_chunks (character_id)",
    """
    CREATE TABLE plot_thread_events (
        id UUID NOT NULL,
        thread_id UUID NOT NULL,
        scene_id UUID,
        chapter_id UUID,
        note TEXT NOT NULL,
        kind VARCHAR(20),
        position INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(thread_id) REFERENCES plot_threads (id) ON DELETE CASCADE,
        FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_plot_thread_events_thread_id ON plot_thread_events (thread_id)",
    """
    CREATE TABLE scene_beats (
        id UUID NOT NULL,
        scene_id UUID NOT NULL,
        beat_number INTEGER NOT NULL,
        description TEXT,
        dialogue JSON,
        content TEXT,
        generation_time_ms INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_scene_beats_beat_number ON scene_beats (beat_number)",
    "CREATE INDEX idx_scene_beats_scene_id ON scene_beats (scene_id)",
    """
    CREATE TABLE scene_revisions (
        id UUID NOT NULL,
        scene_id UUID NOT NULL,
        content TEXT NOT NULL,
        word_count INTEGER,
        source VARCHAR(20) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        PRIMARY KEY (id),
        FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_scene_revisions_scene_id ON scene_revisions (scene_id)",
]


# Vector search lives in the same Postgres via pgvector (ADR-001 amendment).
# Kept OFF the ORM Base (sqlite can't build a vector column), postgres-only.
# book_id is NOT NULL now — voice chunks are book-rooted like their character.
VOICE_CHUNKS: list[str] = [
    """
    CREATE TABLE voice_chunks (
        id UUID PRIMARY KEY,
        character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        user_id UUID NOT NULL,
        book_id UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        chunk_type VARCHAR(50) NOT NULL,
        text TEXT NOT NULL,
        source_location VARCHAR(500),
        word_count INTEGER DEFAULT 0,
        embedding vector(384) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX idx_voice_chunks_character ON voice_chunks (character_id, chunk_type)",
    "CREATE INDEX idx_voice_chunks_book_character ON voice_chunks (book_id, character_id)",
    "CREATE INDEX idx_voice_chunks_embedding "
    "ON voice_chunks USING hnsw (embedding vector_cosine_ops)",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Unit tests never run migrations — conftest builds the schema from the
        # ORM via create_all. This branch is a safety net only; the frozen DDL
        # above is postgres-specific (JSONB, vector).
        from app.core.database import Base
        from app.core import orm_models  # noqa: F401

        Base.metadata.create_all(bind=bind)
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for statement in TABLES:
        op.execute(statement)
    for statement in VOICE_CHUNKS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        from app.core.database import Base
        from app.core import orm_models  # noqa: F401

        Base.metadata.drop_all(bind=bind)
        return

    op.execute("DROP TABLE IF EXISTS voice_chunks")
    for table in (
        "scene_revisions",
        "scene_beats",
        "plot_thread_events",
        "character_chunks",
        "scenes",
        "continuity_reports",
        "characters",
        "sources",
        "plot_threads",
        "chapters",
        "book_plans",
        "books",
        "jobs",
        "api_usage",
        "refresh_tokens",
        "invite_codes",
        "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
