"""SQLAlchemy ORM models for Polyphony database"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    String,
    Integer,
    DateTime,
    ForeignKey,
    Table,
    Text,
    JSON,
    DECIMAL,
    Index,
    UniqueConstraint,
    text,
)

# Generic Uuid type (native uuid on Postgres, CHAR on sqlite test databases)
from sqlalchemy import Uuid as UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import event, select
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from .database import Base


def _soft_delete_column():
    """`deleted_at` for an entity that goes to the Trash (SPEC-13): set on
    delete, cleared on restore; the row only disappears on an explicit purge."""
    return Column(DateTime(timezone=True), nullable=True)


def _live_unique(name: str, *columns: str) -> Index:
    """UNIQUE over live rows only (SPEC-13): a trashed row keeps its name or
    position, but no longer holds it, so a new one can take it. Restoring into
    a taken name is a 409 at the API, never a silent rename."""
    where = text("deleted_at IS NULL")
    return Index(
        name, *columns, unique=True, postgresql_where=where, sqlite_where=where
    )


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
    # Upload format (txt | html | docx | pdf | paste) — paragraph shape differs by
    # format, so evals treat it as a covariate (SPEC-09).
    format = Column(String(20))
    deleted_at = _soft_delete_column()

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
        _live_unique("uq_sources_book_content", "book_id", "content_hash"),
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
    canon_entries = relationship(
        "CanonEntry",
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="CanonEntry.position",
    )
    style_guide = relationship(
        "StyleGuide",
        back_populates="book",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # Scenes are book-rooted (SPEC-04): the DB cascade deletes them with the
    # book, so the ORM need not load a whole manuscript just to delete it.
    scenes = relationship(
        "Scene",
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
    # Promoted from an outline node (SPEC-10): the node's stable id (promote is
    # idempotent on it) and its structural cast, which a new scene defaults to.
    plan_node_id = Column(UUID(as_uuid=True))
    pov_character_id = Column(
        UUID(as_uuid=True), ForeignKey("characters.id", ondelete="SET NULL")
    )
    character_ids = Column(JSON)
    # The chapter's planned scenes as data (SPEC-23): list[PlannedBeat]
    # (app/planning/planned_beat.py). Read through normalize_planned_beats.
    planned_beats = Column(JSON().with_variant(JSONB(), "postgresql"))
    deleted_at = _soft_delete_column()
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    book = relationship("Book", back_populates="chapters")
    scenes = relationship("Scene", back_populates="chapter", order_by="Scene.position")

    @property
    def number(self) -> int:
        """1-based book order of a LIVE chapter: the backend's one "chapter N"
        rule. Live positions are contiguous from 0 because every delete, move
        and restore renumbers them (`renumber_two_phase`), so this equals the
        export's `enumerate(…, 1)` numbering. A trashed chapter keeps its last
        position, and its number is meaningless."""
        return self.position + 1

    __table_args__ = (
        Index("idx_chapters_book_id", "book_id"),
        # One live chapter per outline node: promote is idempotent even when
        # two requests race (SPEC-10). NULL plan_node_ids never collide.
        _live_unique("uq_chapters_book_plan_node", "book_id", "plan_node_id"),
        # Reorder/insert logic renumbers via a two-phase pass (park on negative
        # temp positions, then finalize) so this holds under immediate checking.
        _live_unique("uq_chapters_book_position", "book_id", "position"),
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
    # Other names the text uses for this character ("Mrs. Harker", "the CEL").
    # Fidelity, commit merging and scene casting all honour them (SPEC-09).
    aliases = Column(JSON)
    dialogue_count = Column(Integer, default=0)
    indexed_at = Column(DateTime(timezone=True))
    deleted_at = _soft_delete_column()

    # Relationships
    book = relationship("Book", back_populates="characters")
    source = relationship("Source", back_populates="characters")

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
        _live_unique("uq_characters_book_name", "book_id", "name"),
    )


# A character can receive material from MANY sources: an existing character is
# often re-proposed (and merged) by a later source, and voice chunks now carry
# their own `source_id` provenance. `Character.source_id` records only the FIRST
# source and goes NULL when that file is deleted, so it cannot answer "which
# characters belong to this source's cast?" — a merged character has
# source_id=None yet is genuinely part of the new source. This M2M is the real
# answer, written for every reviewed-commit character (created OR merged), so
# source-detail and the source-scoped generation picker reach them (PR review #2).
source_characters = Table(
    "source_characters",
    Base.metadata,
    Column(
        "source_id",
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "character_id",
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("idx_source_characters_character_id", "character_id"),
)


class Scene(Base):
    __tablename__ = "scenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The root (SPEC-04): a scene is reachable only through its book, and goes
    # with it. Set from the chapter on every create (see _scene_book_from_parent).
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Provenance only, like Character.source_id: deleting the imported file must
    # not delete scenes drafted from it.
    source_id = Column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    # Placement within the book. Nullable only for legacy source-drafted scenes;
    # deleting a chapter through the API moves or deletes its scenes first.
    chapter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="SET NULL"),
        nullable=True,
    )
    position = Column(Integer, nullable=False, default=0)
    title = Column(String(500))
    setting = Column(Text)
    emotional_tone = Column(String(100))
    # JSON (not ARRAY) so sqlite test databases work too. `characters` holds the
    # display names; `character_ids` the validated cast (SPEC-01) — a scene's
    # cast is resolved against its book at the API, never free text.
    characters = Column(JSON)
    character_ids = Column(JSON)
    scene_description = Column(Text)
    scene_request = Column(JSON)
    generated_content = Column(Text)  # immutable generation output
    content = Column(Text)  # the editable draft prose
    word_count = Column(Integer, default=0)
    status = Column(String(50), default="processing")  # processing, completed, failed
    generation_time_ms = Column(Integer)
    evaluation_scores = Column(JSON)
    deleted_at = _soft_delete_column()
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", back_populates="scenes")
    book = relationship("Book", back_populates="scenes")
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

    @property
    def draft(self) -> str:
        """The draft: the raw text the editor loads and saves (the author's edit
        wins over the generation), edge whitespace kept so a save, a revision
        splice and their snapshots round-trip it byte for byte. The export trims
        it (`exports.builder.scene_text`)."""
        return self.content or self.generated_content or ""

    # Indexes
    __table_args__ = (
        Index("idx_scenes_user_id", "user_id"),
        Index("idx_scenes_book_id", "book_id"),
        Index("idx_scenes_source_id", "source_id"),
        Index("idx_scenes_chapter_id", "chapter_id"),
        Index("idx_scenes_status", "status"),
        Index(
            "idx_scenes_created_at", "created_at", postgresql_ops={"created_at": "DESC"}
        ),
        # NULL chapter_id (standalone scenes) is distinct on both Postgres and
        # sqlite, so any number of standalone scenes may share position 0.
        _live_unique("uq_scenes_chapter_position", "chapter_id", "position"),
    )


@event.listens_for(Scene, "before_insert")
def _scene_book_from_parent(mapper, connection, target) -> None:
    """Derive a scene's book from its chapter (else its source) when unset.

    The API sets book_id explicitly; this keeps any other creator — a job, a
    script, a fixture — from tripping NOT NULL or, worse, filing a scene under
    the wrong book. An explicit book_id always wins.
    """
    if target.book_id is not None:
        return
    if target.chapter_id is not None:
        target.book_id = connection.execute(
            select(Chapter.book_id).where(Chapter.id == target.chapter_id)
        ).scalar_one_or_none()
    if target.book_id is None and target.source_id is not None:
        target.book_id = connection.execute(
            select(Source.book_id).where(Source.id == target.source_id)
        ).scalar_one_or_none()


class SceneRevision(Base):
    __tablename__ = "scene_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    # generated | edited | ai_revise (a proposal from POST /scenes/{id}/revise)
    source = Column(String(20), nullable=False, default="edited")
    # AI revision proposals only (SPEC-19): {instruction, range, beat_index,
    # job_id, status: proposed|accepted|discarded|failed|cancelled,
    # base_text_hash, warnings}. NULL for ordinary snapshots.
    meta = Column(JSON().with_variant(JSONB(), "postgresql"))
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
    # Staged generation is a background job (Phase 5): status/stage let the
    # frontend poll GET /books/{id}/plans for progress; warnings carry the
    # fidelity audit's soft findings (never a hard fail).
    status = Column(
        String(32), nullable=False, default="ready"
    )  # ready|generating|paused|awaiting_confirmation|failed|cancelled
    stage = Column(String(30))  # skeleton | chapters | beats | audit
    warnings = Column(JSON)
    error = Column(Text)
    # The staged outline's S1 skeleton (SPEC-10, R1.3): premise_restated,
    # central_conflict, acts — shown to the author, who confirms or edits it
    # before the chapters stage when the run was asked to wait.
    premise = Column(JSON)
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
    # processing | paused | completed | incomplete | failed | cancelled.
    # "incomplete" = some sections could not be checked (SPEC-02): never
    # reported as a clean bill of health.
    status = Column(String(20), nullable=False, default="processing")
    # {"chunks_checked", "chunks_total", "unchecked": [labels]}
    coverage = Column(JSON)
    # list[continuity.Finding] (SPEC-31); pre-SPEC-31 rows carry "refs"
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
    # Lifecycle (SPEC-07). A paused job stays status='queued' (claimable when
    # available_at comes) with paused_since set; the jobs API reports it as
    # 'paused'. paused_since bounds the pause (JOB_MAX_PAUSE_HOURS) and is
    # cleared whenever the job makes progress, so the bound is "no progress for
    # 48h", not "48h in total".
    paused_since = Column(DateTime(timezone=True))
    pause_count = Column(Integer, nullable=False, default=0, server_default="0")
    cancel_requested = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Last completed model call of a multi-call job (SPEC-08). Job-scoped, so a
    # cancelled or failed job leaves no half-written domain state.
    checkpoint = Column(JSON().with_variant(JSONB(), "postgresql"))
    # One in-flight job per key: a duplicate enqueue returns the existing job.
    dedupe_key = Column(String(255))
    # The domain row this job writes (scene | book_plan | extraction_run |
    # continuity_report), so the API can find a row's job and vice versa.
    domain_type = Column(String(30))
    domain_id = Column(UUID(as_uuid=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_jobs_status_available", "status", "available_at"),
        Index("idx_jobs_user_id", "user_id"),
        Index("idx_jobs_kind", "kind"),
        Index("idx_jobs_domain", "domain_type", "domain_id"),
        Index(
            "uq_jobs_dedupe_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
            sqlite_where=text("status IN ('queued','running')"),
        ),
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


class EntityVersion(Base):
    """Append-only version log for canon entities (docs/ADR-002-book-as-root.md §5).

    ONE generic table for every versioned type (book_plan, character, ...). Every
    query is (entity_type, entity_id) -> version_no DESC — never a cross-type
    join — so a SOFT (entity_type, entity_id) pointer is right, while `book_id`
    carries a HARD FK (real cascade) because the book is the root.

    INVARIANT — and it DIVERGES from SceneRevision, so read this before assuming:
    this is an append-only log of every state INCLUDING the current one, so
    max(version_no) for an entity ALWAYS equals its live row. Create -> v1, edit
    -> v2, regenerate -> v3, restore v2 -> APPEND v4 (reason='restored_from:2')
    and set the live row. Restore is forward-only and never deletes.
    (SceneRevision stores history EXCLUDING head — the opposite.) Snapshots are
    full JSON, never diffs; canon is kilobytes and diffs make restore fragile.
    """

    __tablename__ = "entity_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Hard FK — a version cannot outlive its book.
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Soft polymorphic pointer: which entity, of which type. No FK.
    entity_type = Column(String(50), nullable=False)  # book_plan | character | ...
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    version_no = Column(Integer, nullable=False)
    content = Column(JSON, nullable=False)  # full snapshot, never a diff
    # 'created' | 'edited' | 'generated' | 'imported' | 'restored_from:N'
    reason = Column(String(255))
    created_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The real guard against a version_no race — coalesce(max)+1 can collide
        # under concurrency, and this turns that into a retryable IntegrityError.
        UniqueConstraint(
            "entity_type", "entity_id", "version_no", name="uq_entity_versions_ver"
        ),
        Index("idx_entity_versions_lookup", "entity_type", "entity_id", "version_no"),
        Index("idx_entity_versions_book_id", "book_id"),
    )


class CanonEntry(Base):
    """One categorized worldbuilding fact of a book's Canon (docs/BRD.md R3).

    Book is the root, and until now Character was the *only* canon entity — so the
    magic system, the Undeath Pipeline, Aeon Holdings had nowhere to live but the
    synopsis field. One categorized table beats branded section names and is more
    flexible. Versioned via entity_versions (entity_type='canon_entry').
    """

    __tablename__ = "canon_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    # world | location | faction | item | concept | org
    category = Column(String(20), nullable=False, default="concept")
    content = Column(Text)
    position = Column(Integer, nullable=False, default=0)
    deleted_at = _soft_delete_column()
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    book = relationship("Book", back_populates="canon_entries")

    __table_args__ = (
        Index("idx_canon_entries_book_id", "book_id"),
        # A name means one entry within a book — load-bearing for canon_terms
        # (the alias/known-noun allowlist the fidelity grader consumes).
        _live_unique("uq_canon_entries_book_name", "book_id", "name"),
    )


class StyleGuide(Base):
    """A book's prose style: POV, tense, tone, comps, a sample. One per book.

    Feeds staged generation so drafts match the author's intended voice
    (docs/BRD.md R3). Versioned via entity_versions (entity_type='style_guide').
    """

    __tablename__ = "style_guides"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Free-form, not an enum: the UI takes prose ("third-person limited, close on
    # the protagonist, shifting to first in the interludes"), so these are TEXT
    # like their siblings — a short VARCHAR here 500'd on any descriptive POV.
    pov = Column(Text)  # e.g. first | third-limited | third-omniscient | prose
    tense = Column(Text)  # e.g. past | present
    tone = Column(Text)
    comps = Column(Text)  # comparable titles / influences
    sample_prose = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    book = relationship("Book", back_populates="style_guide")

    __table_args__ = (
        # Exactly one style guide per book.
        UniqueConstraint("book_id", name="uq_style_guides_book"),
    )


class ExtractionRun(Base):
    """A Source -> proposed Canon extraction, held for review before commit.

    Extraction PROPOSES; it never writes canon directly (docs/BRD.md R6). The
    LLM's typed candidates (characters / canon entries / style / synopsis) live
    here as JSON until the author edits/approves them, at which point commit
    writes the real entities plus an 'imported' version (Phase 2). Book-rooted.
    """

    __tablename__ = "extraction_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id = Column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    # Provenance — deleting the source must not delete an in-flight review.
    source_id = Column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(
        String(20), nullable=False, default="pending"
    )  # pending | ready | failed | committed
    # {characters:[...], canon_entries:[...], style:{...}, synopsis:"..."}
    proposals = Column(JSON)
    error = Column(Text)
    # Map-reduce progress + resumable checkpoint (SPEC-09):
    # {phase, windows_total, windows_done, ..., checkpoint: {...}}
    progress = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_extraction_runs_book_id", "book_id"),)


class LLMRequestCount(Base):
    """Requests sent to a provider model per vendor-local day (SPEC-03).

    The free tier's binding limit is the vendor's requests-per-day ceiling,
    shared by the whole deployment; app/llm/ledger.py enforces it before the
    call so jobs pause until the vendor's day rolls over."""

    __tablename__ = "llm_request_counts"

    provider = Column(String(50), primary_key=True)
    model = Column(String(100), primary_key=True)
    day = Column(Date, primary_key=True)
    count = Column(Integer, nullable=False, default=0)
