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
)

# Generic Uuid type (native uuid on Postgres, CHAR on sqlite test databases)
from sqlalchemy import Uuid as UUID
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
    manuscripts = relationship(
        "Manuscript", back_populates="user", cascade="all, delete-orphan"
    )
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


class Manuscript(Base):
    __tablename__ = "manuscripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(500), nullable=False)
    author = Column(String(255))
    content_hash = Column(String(64), unique=True)
    file_path = Column(String(1000))
    word_count = Column(Integer)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True))
    status = Column(
        String(50), default="pending"
    )  # pending, processing, completed, failed

    # Relationships
    user = relationship("User", back_populates="manuscripts")
    characters = relationship(
        "Character", back_populates="manuscript", cascade="all, delete-orphan"
    )
    scenes = relationship("Scene", back_populates="manuscript")

    # Indexes
    __table_args__ = (
        Index("idx_manuscripts_user_id", "user_id"),
        Index("idx_manuscripts_status", "status"),
    )


class Character(Base):
    __tablename__ = "characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manuscript_id = Column(
        UUID(as_uuid=True),
        ForeignKey("manuscripts.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text)
    personality_traits = Column(JSON)
    voice_characteristics = Column(JSON)
    dialogue_count = Column(Integer, default=0)
    indexed_at = Column(DateTime(timezone=True))
    qdrant_collection_name = Column(String(255))

    # Relationships
    manuscript = relationship("Manuscript", back_populates="characters")
    chunks = relationship(
        "CharacterChunk", back_populates="character", cascade="all, delete-orphan"
    )

    # Indexes and constraints
    __table_args__ = (
        Index("idx_characters_manuscript_id", "manuscript_id"),
        Index("idx_characters_name", "manuscript_id", "name", unique=True),
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
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    manuscript_id = Column(UUID(as_uuid=True), ForeignKey("manuscripts.id"))
    title = Column(String(500))
    setting = Column(Text)
    emotional_tone = Column(String(100))
    # JSON (not ARRAY) so sqlite test databases work too
    characters = Column(JSON)
    scene_description = Column(Text)
    scene_request = Column(JSON)
    generated_content = Column(Text)
    word_count = Column(Integer, default=0)
    status = Column(String(50), default="processing")  # processing, completed, failed
    generation_time_ms = Column(Integer)
    evaluation_scores = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="scenes")
    manuscript = relationship("Manuscript", back_populates="scenes")
    beats = relationship(
        "SceneBeat", back_populates="scene", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_scenes_user_id", "user_id"),
        Index("idx_scenes_manuscript_id", "manuscript_id"),
        Index("idx_scenes_status", "status"),
        Index(
            "idx_scenes_created_at", "created_at", postgresql_ops={"created_at": "DESC"}
        ),
    )


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
