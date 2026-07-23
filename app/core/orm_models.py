"""SQLAlchemy ORM models for Polyphony database"""

from sqlalchemy import (
    Boolean,
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    DECIMAL,
    Index,
    UniqueConstraint,
)

# Generic Uuid type (native uuid on Postgres, CHAR on sqlite test databases)
from sqlalchemy import Uuid as UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    role = Column(String(20), nullable=False, default="writer", server_default="writer")
    # use_alter breaks the users <-> invite_codes FK cycle at create time
    invite_code_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invite_codes.id", use_alter=True, name="fk_users_invite_code"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    # NOTE: no `sources`/`characters` collection here — Book is the root of every
    # concept (docs/ADR-002-book-as-root.md §1), so they hang off Book and reach
    # the user through it. Their `user_id` columns remain as a tenant guard
    # (migration 0005's deliberate defence-in-depth), not as a second parent.
    books = relationship("Book", back_populates="user", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="user", cascade="all, delete-orphan")
    api_usage = relationship(
        "APIUsage", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(64), unique=True, nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    max_uses = Column(Integer, nullable=False, default=1)
    uses = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    user_agent = Column(String(500))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens")

    __table_args__ = (Index("idx_refresh_tokens_user_id", "user_id"),)


class Source(Base):
    """Raw input material attached to a book: an uploaded file or pasted text.

    Was `Manuscript`, which was user-scoped and sat in a second tree beside Book
    (docs/ADR-002-book-as-root.md §2). A manuscript and a pile of pasted notes
    are the same thing — material that arrived somehow — so they are one entity
    with a `kind`, and you upload INTO a book.

    A Source is disposable; the Canon is not. Deleting the file you imported from
    must never delete your cast, so `Character.source_id` is provenance only
    (ON DELETE SET NULL) and this class deliberately does NOT cascade-delete
    characters. See the note on `characters` below.
    """

    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized tenant guard (migration 0005's pattern), NOT a second parent.
    # Invariant: user_id == book.user_id.
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind = Column(
        String(20), nullable=False, default="upload", server_default="upload"
    )  # upload | paste
    title = Column(String(500), nullable=False)
    author = Column(String(255))
    # content_hash is unique per BOOK, not globally — a global unique leaks a
    # cross-tenant existence oracle (migration 0003). Per-book rather than
    # per-user so the same reference text can feed two different books.
    content_hash = Column(String(64))
    # The parsed text is stored here so (re)processing is driven from the DB, not
    # a container-local file that dies on restart/idle-reclaim.
    content_text = Column(Text)
    file_path = Column(String(1000))
    word_count = Column(Integer)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True))
    status = Column(
        String(50), default="pending"
    )  # pending, processing, completed, failed

    # Relationships
    book = relationship("Book", back_populates="sources")
    # NO cascade="all, delete-orphan" here — deliberate. The DB FK is
    # ON DELETE SET NULL, and if this ORM side still cascaded, SQLAlchemy would
    # delete the characters in Python anyway and the FK change would be a lie.
    # Both halves must agree (docs/ADR-002-book-as-root.md §2).
    characters = relationship("Character", back_populates="source")
    scenes = relationship("Scene", back_populates="source")

    # Indexes
    __table_args__ = (
        Index("idx_sources_book_id", "book_id"),
        Index("idx_sources_user_id", "user_id"),
        Index("idx_sources_status", "status"),
        UniqueConstraint("book_id", "content_hash", name="uq_sources_book_content"),
    )


class Book(Base):
    __tablename__ = "books"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(500), nullable=False)
    author = Column(String(255))
    synopsis = Column(Text)
    genre = Column(String(100))
    status = Column(
        String(50), nullable=False, default="drafting"
    )  # drafting, revising, complete
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="books")
    chapters = relationship(
        "Chapter",
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.position",
    )
    # The Canon. `characters` did not exist here at all until now: the column was
    # on Character, nothing ever wrote it, and the outline's bible query filtered
    # on it — so it always returned zero rows and the model never saw a cast.
    # That silence is what produced "Elara" (docs/BRD.md §1).
    characters = relationship(
        "Character",
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Character.name",
    )
    sources = relationship(
        "Source",
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    plans = relationship(
        "BookPlan", back_populates="book", cascade="all, delete-orphan"
    )
    threads = relationship(
        "PlotThread", back_populates="book", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_books_user_id", "user_id"),)


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    position = Column(Integer, nullable=False, default=0)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    status = Column(String(50), nullable=False, default="drafting")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    book = relationship("Book", back_populates="chapters")
    scenes = relationship("Scene", back_populates="chapter", order_by="Scene.position")

    __table_args__ = (
        Index("idx_chapters_book_id", "book_id"),
        # Reorder/insert logic renumbers via a two-phase pass (park on negative
        # temp positions, then finalize) so this holds under immediate checking.
        UniqueConstraint("book_id", "position", name="uq_chapters_book_position"),
    )


class Character(Base):
    """A character belongs to exactly ONE book. It is Canon.

    This used to read "a character belongs to a user's bible… book_id scopes a
    character to one book WHEN SET" — and nothing ever set it. The column was
    nullable, written by no code path, and queried by two features that
    therefore always saw an empty cast (docs/BRD.md §1). It is now NOT NULL and
    the book is the real parent (docs/ADR-002-book-as-root.md §1).
    """

    __tablename__ = "characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # CASCADE, not SET NULL: with book_id NOT NULL, SET NULL would violate the
    # constraint and 500 the book-delete endpoint.
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized tenant guard (migration 0005), NOT a second parent.
    # Invariant: user_id == book.user_id.
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Provenance only: which Source this character was extracted from, if any.
    # SET NULL — deleting an imported file must never delete the cast it seeded.
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text)
    personality_traits = Column(JSON)
    voice_characteristics = Column(JSON)
    # Character-bible fields
    role = Column(String(100))  # protagonist, antagonist, supporting, ...
    goals = Column(Text)
    arc = Column(Text)
    relationships = Column(JSON)  # {"other name": "relationship"}
    notes = Column(Text)
    dialogue_count = Column(Integer, default=0)
    indexed_at = Column(DateTime(timezone=True))

    # Relationships
    book = relationship("Book", back_populates="characters")
    source = relationship("Source", back_populates="characters")
    chunks = relationship(
        "CharacterChunk", back_populates="character", cascade="all, delete-orphan"
    )

    # Indexes and constraints
    __table_args__ = (
        Index("idx_characters_book_id", "book_id"),
        Index("idx_characters_source_id", "source_id"),
        Index("idx_characters_user_id", "user_id"),
        # Names are unique within a BOOK. The old index keyed on manuscript_id,
        # which is NULL for every manually-created character — and NULLs are
        # distinct in Postgres, so manual characters had NO uniqueness at all.
        # Cast-fidelity checking (docs/BRD.md R1.4) needs a name to mean one
        # person, so this is load-bearing, not tidiness.
        UniqueConstraint("book_id", "name", name="uq_characters_book_name"),
    )


class CharacterChunk(Base):
    __tablename__ = "character_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id = Column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_type = Column(String(50))  # dialogue, action, thought, description
    content = Column(Text, nullable=False)
    source_location = Column(String(500))
    embedding_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    character = relationship("Character", back_populates="chunks")

    # Indexes
    __table_args__ = (Index("idx_character_chunks_character_id", "character_id"),)


class Scene(Base):
    __tablename__ = "scenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Provenance only, like Character.source_id: deleting the imported file must
    # not delete scenes drafted from it.
    source_id = Column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    # Book placement (nullable: standalone scenes exist outside any chapter)
    chapter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="SET NULL"),
        nullable=True,
    )
    position = Column(Integer, nullable=False, default=0)
    title = Column(String(500))
    setting = Column(Text)
    emotional_tone = Column(String(100))
    # JSON (not ARRAY) so sqlite test databases work too
    characters = Column(JSON)
    scene_description = Column(Text)
    scene_request = Column(JSON)
    generated_content = Column(Text)  # immutable generation output
    content = Column(Text)  # the editable draft prose
    word_count = Column(Integer, default=0)
    status = Column(String(50), default="processing")  # processing, completed, failed
    generation_time_ms = Column(Integer)
    evaluation_scores = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="scenes")
    source = relationship("Source", back_populates="scenes")
    chapter = relationship("Chapter", back_populates="scenes")
    beats = relationship(
        "SceneBeat", back_populates="scene", cascade="all, delete-orphan"
    )
    revisions = relationship(
        "SceneRevision",
        back_populates="scene",
        cascade="all, delete-orphan",
        order_by="SceneRevision.created_at",
    )

    # Indexes
    __table_args__ = (
        Index("idx_scenes_user_id", "user_id"),
        Index("idx_scenes_source_id", "source_id"),
        Index("idx_scenes_chapter_id", "chapter_id"),
        Index("idx_scenes_status", "status"),
        Index(
            "idx_scenes_created_at", "created_at", postgresql_ops={"created_at": "DESC"}
        ),
        # NULL chapter_id (standalone scenes) is distinct on both Postgres and
        # sqlite, so any number of standalone scenes may share position 0.
        UniqueConstraint("chapter_id", "position", name="uq_scenes_chapter_position"),
    )


class SceneRevision(Base):
    __tablename__ = "scene_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    source = Column(String(20), nullable=False, default="edited")  # generated | edited
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    scene = relationship("Scene", back_populates="revisions")

    __table_args__ = (Index("idx_scene_revisions_scene_id", "scene_id"),)


class SceneBeat(Base):
    __tablename__ = "scene_beats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    beat_number = Column(Integer, nullable=False)
    description = Column(Text)
    dialogue = Column(JSON)  # Array of dialogue turns
    content = Column(Text)
    generation_time_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scene = relationship("Scene", back_populates="beats")

    # Indexes
    __table_args__ = (
        Index("idx_scene_beats_scene_id", "scene_id"),
        Index("idx_scene_beats_beat_number", "beat_number"),
    )


class BookPlan(Base):
    __tablename__ = "book_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    kind = Column(String(20), nullable=False, default="outline")  # outline | beat_sheet
    # Ordered plan nodes: [{"title", "summary", "children": [...]}, ...]
    content = Column(JSON, nullable=False, default=list)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    book = relationship("Book", back_populates="plans")

    __table_args__ = (Index("idx_book_plans_book_id", "book_id"),)


class PlotThread(Base):
    __tablename__ = "plot_threads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(
        String(20), nullable=False, default="open"
    )  # open | resolved | abandoned
    color = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    book = relationship("Book", back_populates="threads")
    events = relationship(
        "PlotThreadEvent",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="PlotThreadEvent.position",
    )

    __table_args__ = (Index("idx_plot_threads_book_id", "book_id"),)


class PlotThreadEvent(Base):
    __tablename__ = "plot_thread_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plot_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_id = Column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True
    )
    chapter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
    )
    note = Column(Text, nullable=False)
    kind = Column(String(20), default="development")  # setup | development | payoff
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    thread = relationship("PlotThread", back_populates="events")

    __table_args__ = (Index("idx_plot_thread_events_thread_id", "thread_id"),)


class ContinuityReport(Base):
    __tablename__ = "continuity_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
    )
    scope = Column(String(20), nullable=False, default="chapter")  # chapter | book
    status = Column(String(20), nullable=False, default="processing")
    # [{"type": "timeline|character|object|thread", "severity", "detail", "refs"}]
    findings = Column(JSON)
    model = Column(String(100))
    tokens_used = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_continuity_reports_book_id", "book_id"),)


class Job(Base):
    """Durable background job.

    Replaces FastAPI BackgroundTasks for long-running LLM/manuscript work:
    a job row commits atomically with its domain row (scene/manuscript/report),
    survives process restarts, and is executed by the single in-process worker
    loop (app/jobs/worker.py) which claims via FOR UPDATE SKIP LOCKED.
    """

    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # process_manuscript | generate_scene | generate_prose_scene | continuity_check
    kind = Column(String(50), nullable=False)
    # JSON on sqlite (tests), JSONB on Postgres — same convention as other
    # JSON columns, upgraded where the dialect supports it.
    payload = Column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    status = Column(
        String(20), nullable=False, default="queued"
    )  # queued | running | succeeded | dead
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=1)
    locked_at = Column(DateTime(timezone=True))
    locked_by = Column(String(100))
    available_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_jobs_status_available", "status", "available_at"),
        Index("idx_jobs_user_id", "user_id"),
        Index("idx_jobs_kind", "kind"),
    )


class APIUsage(Base):
    __tablename__ = "api_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    endpoint = Column(String(255))
    tokens_used = Column(Integer)
    cost_usd = Column(DECIMAL(10, 6))
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="api_usage")

    # The rolling-24h budget check filters WHERE user_id = ? AND timestamp >= ?
    # on every LLM-spending request — keep that hot path indexed.
    __table_args__ = (Index("idx_api_usage_user_time", "user_id", "timestamp"),)
