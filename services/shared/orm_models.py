"""SQLAlchemy ORM models for Polyphony database"""

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, ARRAY, Text, JSON, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    manuscripts = relationship("Manuscript", back_populates="user", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="user", cascade="all, delete-orphan")
    api_usage = relationship("APIUsage", back_populates="user", cascade="all, delete-orphan")


class Manuscript(Base):
    __tablename__ = "manuscripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    author = Column(String(255))
    content_hash = Column(String(64), unique=True)
    file_path = Column(String(1000))
    word_count = Column(Integer)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True))
    status = Column(String(50), default="pending")  # pending, processing, completed, failed

    # Relationships
    user = relationship("User", back_populates="manuscripts")
    characters = relationship("Character", back_populates="manuscript", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="manuscript")

    # Indexes
    __table_args__ = (
        Index('idx_manuscripts_user_id', 'user_id'),
        Index('idx_manuscripts_status', 'status'),
    )


class Character(Base):
    __tablename__ = "characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manuscript_id = Column(UUID(as_uuid=True), ForeignKey("manuscripts.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    personality_traits = Column(JSON)
    voice_characteristics = Column(JSON)
    dialogue_count = Column(Integer, default=0)
    indexed_at = Column(DateTime(timezone=True))
    qdrant_collection_name = Column(String(255))

    # Relationships
    manuscript = relationship("Manuscript", back_populates="characters")
    chunks = relationship("CharacterChunk", back_populates="character", cascade="all, delete-orphan")

    # Indexes and constraints
    __table_args__ = (
        Index('idx_characters_manuscript_id', 'manuscript_id'),
        Index('idx_characters_name', 'manuscript_id', 'name', unique=True),
    )


class CharacterChunk(Base):
    __tablename__ = "character_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id = Column(UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    chunk_type = Column(String(50))  # dialogue, action, thought, description
    content = Column(Text, nullable=False)
    source_location = Column(String(500))
    embedding_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    character = relationship("Character", back_populates="chunks")

    # Indexes
    __table_args__ = (
        Index('idx_character_chunks_character_id', 'character_id'),
    )


class Scene(Base):
    __tablename__ = "scenes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    manuscript_id = Column(UUID(as_uuid=True), ForeignKey("manuscripts.id"))
    title = Column(String(500))
    setting = Column(Text)
    emotional_tone = Column(String(100))
    characters = Column(ARRAY(String(255)))
    scene_description = Column(Text)
    scene_request = Column(JSON)
    generated_content = Column(Text)
    word_count = Column(Integer, default=0)
    status = Column(String(50), default='processing')  # processing, completed, failed
    characters_involved = Column(ARRAY(String(255)))  # Legacy field
    generation_time_ms = Column(Integer)
    evaluation_scores = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="scenes")
    manuscript = relationship("Manuscript", back_populates="scenes")
    beats = relationship("SceneBeat", back_populates="scene", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_scenes_user_id', 'user_id'),
        Index('idx_scenes_manuscript_id', 'manuscript_id'),
        Index('idx_scenes_status', 'status'),
        Index('idx_scenes_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
    )


class SceneBeat(Base):
    __tablename__ = "scene_beats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False)
    beat_number = Column(Integer, nullable=False)
    description = Column(Text)
    dialogue = Column(JSON)  # Array of dialogue turns
    beat_index = Column(Integer)  # Legacy field
    beat_description = Column(Text)  # Legacy field
    characters_involved = Column(ARRAY(String(255)))
    content = Column(Text)
    generation_time_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scene = relationship("Scene", back_populates="beats")

    # Indexes
    __table_args__ = (
        Index('idx_scene_beats_scene_id', 'scene_id'),
        Index('idx_scene_beats_beat_number', 'beat_number'),
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
