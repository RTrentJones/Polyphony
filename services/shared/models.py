"""Shared Pydantic models for Polyphony"""

from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum


# Enums
class ManuscriptStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ChunkType(str, Enum):
    DIALOGUE = "dialogue"
    ACTION = "action"
    THOUGHT = "thought"
    DESCRIPTION = "description"


# User models
class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


# Manuscript models
class ManuscriptCreate(BaseModel):
    title: str
    author: Optional[str] = None


class Manuscript(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    author: Optional[str]
    word_count: Optional[int]
    status: ManuscriptStatus
    uploaded_at: datetime
    processed_at: Optional[datetime]

    class Config:
        from_attributes = True


# Character models
class CharacterProfile(BaseModel):
    name: str
    description: Optional[str] = None
    personality_traits: Dict[str, Any] = {}
    voice_characteristics: Dict[str, Any] = {}


class Character(CharacterProfile):
    id: UUID
    manuscript_id: UUID
    dialogue_count: int = 0
    indexed_at: Optional[datetime]
    qdrant_collection_name: Optional[str]

    class Config:
        from_attributes = True


# Scene request models
class SceneRequest(BaseModel):
    manuscript_id: UUID
    characters: List[str] = Field(..., min_length=1)
    scene_description: str = Field(..., min_length=10)
    setting: str
    emotional_tone: str
    pov_character: Optional[str] = None
    target_word_count: int = Field(default=500, ge=100, le=3000)
    style_notes: Optional[str] = None


class SceneBeat(BaseModel):
    beat_index: int
    beat_description: str
    characters_involved: List[str]
    emotional_shift: Optional[str] = None
    plot_objective: Optional[str] = None


class SceneGenerationState(BaseModel):
    """State for LangGraph orchestrator"""

    scene_request: SceneRequest
    scene_beats: List[SceneBeat] = []
    current_beat_index: int = 0
    character_turns: List[Dict[str, Any]] = []
    generated_content: List[str] = []
    final_scene: str = ""
    metadata: Dict[str, Any] = {}


# Character agent models
class DialogueRequest(BaseModel):
    character_name: str
    scene_context: Dict[str, Any]
    emotional_state: str
    other_characters: List[str]
    beat_description: str
    previous_dialogue: List[Dict[str, str]] = []


class DialogueResponse(BaseModel):
    character: str
    dialogue: str
    action: Optional[str] = None
    internal_thought: Optional[str] = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    retrieved_examples: List[str] = []


# Evaluation models
class EvaluationMetrics(BaseModel):
    voice_consistency: float = Field(ge=0.0, le=1.0)
    narrative_flow: float = Field(ge=0.0, le=1.0)
    dialogue_naturality: float = Field(ge=0.0, le=1.0)
    scene_coherence: float = Field(ge=0.0, le=1.0)
    emotional_arc: float = Field(ge=0.0, le=1.0)
    overall_score: float = Field(ge=0.0, le=1.0)


class SceneEvaluation(BaseModel):
    scene_id: UUID
    metrics: EvaluationMetrics
    detailed_feedback: Optional[str] = None
    evaluated_at: datetime


# Streaming response models
class StreamEvent(BaseModel):
    event_type: (
        str  # beat_start, character_generating, dialogue_complete, scene_complete
    )
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Qdrant models
class CharacterChunkMetadata(BaseModel):
    character_id: str
    character_name: str
    manuscript_id: str
    chunk_type: ChunkType
    source_location: Optional[str]
    word_count: int
    created_at: datetime
