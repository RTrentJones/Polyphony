# Polyphony
A project for agentic creative writing assistance.


# **Multi-Character RAG Creative Writing Platform: Technical Design Document**

**Project Name**: Polyphony  
**Version**: 1.0  
**Author**: Technical Architecture Team  
**Last Updated**: November 2024  
**Target**: Claude Code Implementation

---

## **Executive Summary**

Build a production-grade multi-agent creative writing system that uses character-specific RAG (Retrieval-Augmented Generation) to maintain voice consistency. The system coordinates multiple character agents through a narrative orchestrator to generate coherent scenes, deployed on Kubernetes with full observability.

**Key Differentiators**:
- Character-specific vector stores for voice consistency
- LangGraph-based multi-agent coordination
- Kubernetes-native with auto-scaling
- Real-time streaming UI showing agent status
- Comprehensive monitoring and evaluation

---

## **Table of Contents**

1. [System Architecture](#1-system-architecture)
2. [Technology Stack](#2-technology-stack)
3. [Data Models & Schemas](#3-data-models--schemas)
4. [API Specifications](#4-api-specifications)
5. [Component Specifications](#5-component-specifications)
6. [File Structure](#6-file-structure)
7. [Implementation Phases](#7-implementation-phases)
8. [Testing Strategy](#8-testing-strategy)
9. [Deployment Specifications](#9-deployment-specifications)
10. [Monitoring & Observability](#10-monitoring--observability)

---

## **1. System Architecture**

### **1.1 High-Level Architecture**

```
┌─────────────────────────────────────────────────────────────┐
│                         User Layer                          │
│  Next.js Frontend with Real-time Streaming (Port 3000)     │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP/WebSocket
                     ▼
┌─────────────────────────────────────────────────────────────┐
│                     API Gateway Layer                       │
│        FastAPI (Port 8000) - Request routing & auth        │
└────────────────────┬────────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
┌──────────────────┐   ┌──────────────────────────────────────┐
│  Orchestrator    │   │    Character Agent Services          │
│  Service         │◄──┤  (Multiple instances per character)  │
│  (LangGraph)     │   │  - Hermione (Port 8002)              │
│  Port: 8001      │   │  - Harry (Port 8003)                 │
└──────────────────┘   │  - Ron (Port 8004)                   │
          │            └──────────────────────────────────────┘
          │                     │
          └─────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐   ┌──────────────────┐
│  Qdrant Vector   │   │  Redis Cache +   │
│  Database        │   │  Message Queue   │
│  (Character      │   │  (Port 6379)     │
│   collections)   │   └──────────────────┘
│  Port: 6333      │            │
└──────────────────┘            ▼
          │            ┌──────────────────┐
          │            │  PostgreSQL      │
          │            │  (Metadata,      │
          │            │   User data)     │
          └───────────►│  Port: 5432      │
                       └──────────────────┘
```

### **1.2 Component Interactions**

**Scene Generation Flow**:
1. User submits scene request → Frontend
2. Frontend → API Gateway (HTTP POST)
3. API Gateway → Orchestrator Service
4. Orchestrator:
   - Parses request into scene beats
   - For each beat:
     - Sends context to relevant Character Agents
     - Character Agents retrieve from their RAG
     - Character Agents generate dialogue/actions
   - Synthesizes responses into coherent scene
5. Streams results back through WebSocket
6. Frontend displays real-time generation progress

### **1.3 Data Flow**

```
Manuscript Upload → Document Parser → Character Extractor
                                              │
                                              ▼
                                    [For each character]
                                              │
                        ┌─────────────────────┴──────────────────┐
                        ▼                                        ▼
                  Dialogue chunks                         Action chunks
                        │                                        │
                        └────────►  Embeddings  ◄────────────────┘
                                        │
                                        ▼
                            Character-specific collection
                                  in Qdrant
```

---

## **2. Technology Stack**

### **2.1 Core Technologies**

| Component | Technology | Version | Rationale |
|-----------|-----------|---------|-----------|
| Frontend | Next.js | 15.x | SSR, streaming, Vercel AI SDK |
| API Gateway | FastAPI | 0.104+ | Async, auto-docs, Python ecosystem |
| Orchestrator | LangGraph | 0.2+ | State machine for multi-agent coordination |
| Character Agents | FastAPI + LangChain | Latest | Microservice architecture |
| LLM Provider | Groq | Latest | 14,400 free requests/day, fast inference |
| Vector DB | Qdrant | 1.7+ | Hybrid search, filtering, 1M free vectors |
| Embeddings | sentence-transformers | Latest | all-MiniLM-L6-v2 (free, fast) |
| Cache/Queue | Redis | 7.x | Fast in-memory cache |
| Database | PostgreSQL | 16.x | Relational data, metadata |
| Container Runtime | Docker | 24.x | Containerization |
| Orchestration | Kubernetes | 1.28+ | Production deployment |
| Monitoring | Prometheus + Grafana | Latest | Metrics and dashboards |

### **2.2 Python Dependencies**

```python
# requirements.txt (shared across services)
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
groq==0.4.0
langchain==0.1.0
langgraph==0.0.40
langchain-groq==0.0.1
qdrant-client==1.7.0
sentence-transformers==2.2.2
redis==5.0.1
asyncpg==0.29.0
sqlalchemy==2.0.23
python-multipart==0.0.6
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-docx==1.1.0
PyPDF2==3.0.1
beautifulsoup4==4.12.2
prometheus-client==0.19.0
aiofiles==23.2.1
```

### **2.3 Frontend Dependencies**

```json
// package.json
{
  "dependencies": {
    "next": "15.0.0",
    "react": "18.3.0",
    "react-dom": "18.3.0",
    "ai": "^3.0.0",
    "@vercel/analytics": "^1.1.0",
    "zustand": "^4.4.0",
    "tailwindcss": "^3.3.0",
    "lucide-react": "^0.263.0",
    "react-markdown": "^9.0.0",
    "framer-motion": "^10.16.0"
  }
}
```

---

## **3. Data Models & Schemas**

### **3.1 Database Schema (PostgreSQL)**

```sql
-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Manuscripts table
CREATE TABLE manuscripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    author VARCHAR(255),
    content_hash VARCHAR(64) UNIQUE, -- SHA256 of content
    file_path VARCHAR(1000),
    word_count INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    status VARCHAR(50) DEFAULT 'pending' -- pending, processing, completed, failed
);

-- Characters table
CREATE TABLE characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manuscript_id UUID REFERENCES manuscripts(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    personality_traits JSONB, -- {"brave": true, "witty": true}
    voice_characteristics JSONB, -- {"avg_sentence_length": 15, "uses_contractions": true}
    dialogue_count INTEGER DEFAULT 0,
    indexed_at TIMESTAMP,
    qdrant_collection_name VARCHAR(255),
    UNIQUE(manuscript_id, name)
);

-- Character content chunks (for tracking/debugging)
CREATE TABLE character_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    character_id UUID REFERENCES characters(id) ON DELETE CASCADE,
    chunk_type VARCHAR(50), -- dialogue, action, thought, description
    content TEXT NOT NULL,
    source_location VARCHAR(500), -- chapter, page, scene
    embedding_id VARCHAR(255), -- Qdrant point ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scene generation history
CREATE TABLE scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    manuscript_id UUID REFERENCES manuscripts(id),
    scene_request JSONB NOT NULL, -- full request payload
    generated_content TEXT,
    characters_involved VARCHAR(255)[], -- array of character names
    generation_time_ms INTEGER,
    evaluation_scores JSONB, -- voice_consistency, coherence, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scene beats (sub-parts of scenes)
CREATE TABLE scene_beats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scene_id UUID REFERENCES scenes(id) ON DELETE CASCADE,
    beat_index INTEGER NOT NULL,
    beat_description TEXT,
    characters_involved VARCHAR(255)[],
    content TEXT,
    generation_time_ms INTEGER
);

-- API usage tracking
CREATE TABLE api_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    endpoint VARCHAR(255),
    tokens_used INTEGER,
    cost_usd DECIMAL(10, 6),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_manuscripts_user_id ON manuscripts(user_id);
CREATE INDEX idx_characters_manuscript_id ON characters(manuscript_id);
CREATE INDEX idx_scenes_user_id ON scenes(user_id);
CREATE INDEX idx_scenes_created_at ON scenes(created_at DESC);
CREATE INDEX idx_character_chunks_character_id ON character_chunks(character_id);
```

### **3.2 Pydantic Models**

```python
# shared/models.py
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
    characters: List[str] = Field(..., min_items=1)
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
    event_type: str  # beat_start, character_generating, dialogue_complete, scene_complete
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
```

### **3.3 Qdrant Collection Schema**

```python
# Each character gets their own collection
# Collection name: f"character_{character_id}"

from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

collection_config = {
    "vectors": VectorParams(
        size=384,  # all-MiniLM-L6-v2 dimension
        distance=Distance.COSINE
    ),
    "payload_schema": {
        "character_id": PayloadSchemaType.KEYWORD,
        "character_name": PayloadSchemaType.KEYWORD,
        "chunk_type": PayloadSchemaType.KEYWORD,  # dialogue, action, thought
        "text": PayloadSchemaType.TEXT,
        "source_location": PayloadSchemaType.TEXT,
        "word_count": PayloadSchemaType.INTEGER,
        "emotional_tone": PayloadSchemaType.KEYWORD,  # optional
        "timestamp": PayloadSchemaType.FLOAT
    }
}
```

---

## **4. API Specifications**

### **4.1 API Gateway Endpoints**

**Base URL**: `http://api-gateway:8000`

#### **Authentication**

```yaml
POST /api/v1/auth/register
Request:
  email: string
  password: string
  full_name: string (optional)
Response: 
  user: User
  access_token: string
  token_type: "bearer"

POST /api/v1/auth/login
Request:
  email: string
  password: string
Response:
  access_token: string
  token_type: "bearer"
  
GET /api/v1/auth/me
Headers:
  Authorization: Bearer {token}
Response:
  user: User
```

#### **Manuscript Management**

```yaml
POST /api/v1/manuscripts/upload
Headers:
  Authorization: Bearer {token}
Request (multipart/form-data):
  file: File (.docx, .pdf, .txt)
  title: string
  author: string (optional)
Response:
  manuscript: Manuscript
  message: "Processing started"

GET /api/v1/manuscripts
Headers:
  Authorization: Bearer {token}
Query params:
  skip: int = 0
  limit: int = 20
Response:
  manuscripts: List[Manuscript]
  total: int

GET /api/v1/manuscripts/{manuscript_id}
Response:
  manuscript: Manuscript

GET /api/v1/manuscripts/{manuscript_id}/characters
Response:
  characters: List[Character]

POST /api/v1/manuscripts/{manuscript_id}/process
Description: Manually trigger processing if failed
Response:
  status: string
  message: string
```

#### **Scene Generation**

```yaml
POST /api/v1/scenes/generate
Headers:
  Authorization: Bearer {token}
Request:
  scene_request: SceneRequest
Response (Server-Sent Events stream):
  Stream of StreamEvent objects
  
Example stream:
  event: beat_start
  data: {"beat_index": 0, "description": "Characters enter room"}
  
  event: character_generating
  data: {"character": "Hermione", "status": "retrieving"}
  
  event: dialogue_complete
  data: {"character": "Hermione", "dialogue": "...", "action": "..."}
  
  event: scene_complete
  data: {"scene_id": "uuid", "content": "...", "metrics": {...}}

GET /api/v1/scenes
Headers:
  Authorization: Bearer {token}
Query params:
  manuscript_id: UUID (optional)
  skip: int = 0
  limit: int = 20
Response:
  scenes: List[Scene]
  total: int

GET /api/v1/scenes/{scene_id}
Response:
  scene: Scene with full content
  evaluation: SceneEvaluation
```

#### **Character Testing**

```yaml
POST /api/v1/characters/{character_id}/test-dialogue
Request:
  prompt: string
  context: string (optional)
Response:
  dialogue: string
  action: string
  confidence_score: float
  retrieved_examples: List[str]
```

### **4.2 Orchestrator Service API**

**Base URL**: `http://orchestrator:8001`

```yaml
POST /orchestrate
Request:
  scene_request: SceneRequest
Response (streaming):
  Stream of orchestration updates

GET /health
Response:
  status: "healthy"
  version: string
  
GET /metrics
Response:
  Prometheus metrics
```

### **4.3 Character Agent API**

**Base URL**: `http://{character-service}:{port}`

```yaml
POST /generate-dialogue
Request:
  DialogueRequest
Response:
  DialogueResponse

POST /generate-action
Request:
  scene_context: Dict
  dialogue: string
Response:
  action: string
  confidence_score: float

GET /retrieve-examples
Query params:
  query: string
  k: int = 5
Response:
  examples: List[str]
  scores: List[float]

GET /health
Response:
  status: "healthy"
  character: string
  collection_size: int

GET /metrics
Response:
  Prometheus metrics
```

---

## **5. Component Specifications**

### **5.1 Document Parser**

**Purpose**: Extract and structure content from uploaded manuscripts

**Location**: `services/document-parser/`

**Key Functions**:

```python
# document_parser/parser.py
from typing import List, Dict
import docx
import PyPDF2
from bs4 import BeautifulSoup

class DocumentParser:
    """Parse various document formats"""
    
    def parse_document(self, file_path: str) -> str:
        """
        Parse document based on extension
        Returns: Full text content
        """
        ext = file_path.split('.')[-1].lower()
        
        if ext == 'docx':
            return self._parse_docx(file_path)
        elif ext == 'pdf':
            return self._parse_pdf(file_path)
        elif ext == 'txt':
            return self._parse_txt(file_path)
        elif ext in ['html', 'htm']:
            return self._parse_html(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")
    
    def _parse_docx(self, file_path: str) -> str:
        """Parse DOCX file"""
        doc = docx.Document(file_path)
        return '\n\n'.join([para.text for para in doc.paragraphs])
    
    def _parse_pdf(self, file_path: str) -> str:
        """Parse PDF file"""
        text = []
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                text.append(page.extract_text())
        return '\n\n'.join(text)
    
    def _parse_txt(self, file_path: str) -> str:
        """Parse TXT file"""
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    def _parse_html(self, file_path: str) -> str:
        """Parse HTML file"""
        with open(file_path, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file.read(), 'html.parser')
            return soup.get_text()

class CharacterExtractor:
    """Extract character-specific content from manuscript"""
    
    def __init__(self, llm_client):
        self.llm = llm_client
    
    async def extract_characters(self, text: str) -> List[str]:
        """
        Use LLM to identify main characters in text
        Returns: List of character names
        """
        prompt = f"""Analyze this manuscript excerpt and list the main characters (limit to top 10).
        
Text:
{text[:10000]}  # First 10k chars for character identification

Return ONLY a JSON list of character names, no other text:
["Character1", "Character2", ...]
"""
        
        response = await self.llm.generate(prompt)
        import json
        return json.loads(response)
    
    def extract_character_content(
        self, 
        text: str, 
        character_name: str
    ) -> List[Dict[str, str]]:
        """
        Extract all content related to specific character
        Returns: List of chunks with metadata
        """
        chunks = []
        
        # Split into paragraphs
        paragraphs = text.split('\n\n')
        
        for i, para in enumerate(paragraphs):
            # Check if character appears
            if character_name.lower() in para.lower():
                chunk_type = self._classify_chunk(para, character_name)
                
                chunks.append({
                    'text': para.strip(),
                    'chunk_type': chunk_type,
                    'source_location': f'paragraph_{i}',
                    'character_name': character_name
                })
        
        return chunks
    
    def _classify_chunk(self, text: str, character_name: str) -> str:
        """
        Classify chunk as dialogue, action, thought, or description
        """
        # Simple heuristics (can be enhanced with LLM)
        if '"' in text or "'" in text:
            return 'dialogue'
        elif 'thought' in text.lower() or 'wondered' in text.lower():
            return 'thought'
        elif any(verb in text.lower() for verb in ['walked', 'ran', 'jumped', 'grabbed']):
            return 'action'
        else:
            return 'description'
    
    def extract_dialogue_only(
        self, 
        text: str, 
        character_name: str
    ) -> List[str]:
        """
        Extract only dialogue lines for this character
        More sophisticated than extract_character_content
        """
        import re
        
        dialogues = []
        
        # Pattern: "Character said/asked/etc: "dialogue"
        # or just: "dialogue" near character name
        patterns = [
            rf'{character_name}[^"]*"([^"]+)"',
            rf'"{character_name}[^"]*"([^"]+)"',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                dialogues.append(match.group(1))
        
        return dialogues
```

### **5.2 Character RAG System**

**Purpose**: Manage character-specific vector stores and retrieval

**Location**: `services/character-agent/rag_system.py`

```python
# character-agent/rag_system.py
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition
from sentence_transformers import SentenceTransformer
from typing import List, Dict, Optional
import uuid
from datetime import datetime

class CharacterRAG:
    """RAG system for individual character"""
    
    def __init__(
        self,
        character_id: str,
        character_name: str,
        qdrant_url: str
    ):
        self.character_id = character_id
        self.character_name = character_name
        self.collection_name = f"character_{character_id}"
        
        self.qdrant = AsyncQdrantClient(url=qdrant_url)
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    
    async def create_collection(self):
        """Create Qdrant collection for this character"""
        await self.qdrant.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=384,  # all-MiniLM-L6-v2
                distance=Distance.COSINE
            )
        )
    
    async def index_character_content(
        self, 
        chunks: List[Dict[str, str]]
    ):
        """
        Index character content chunks
        chunks: List of dicts with 'text', 'chunk_type', 'source_location'
        """
        points = []
        
        for chunk in chunks:
            # Generate embedding
            embedding = self.embedding_model.encode(chunk['text'])
            
            # Create point
            point = PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding.tolist(),
                payload={
                    'character_id': self.character_id,
                    'character_name': self.character_name,
                    'chunk_type': chunk['chunk_type'],
                    'text': chunk['text'],
                    'source_location': chunk.get('source_location', ''),
                    'word_count': len(chunk['text'].split()),
                    'timestamp': datetime.utcnow().timestamp()
                }
            )
            points.append(point)
        
        # Batch upload
        await self.qdrant.upsert(
            collection_name=self.collection_name,
            points=points
        )
    
    async def retrieve_similar_dialogue(
        self,
        query: str,
        k: int = 5,
        chunk_type: Optional[str] = None
    ) -> List[Dict]:
        """
        Retrieve similar past content for voice consistency
        """
        # Generate query embedding
        query_vector = self.embedding_model.encode(query).tolist()
        
        # Build filter
        filters = None
        if chunk_type:
            filters = Filter(
                must=[
                    FieldCondition(
                        key="chunk_type",
                        match={"value": chunk_type}
                    )
                ]
            )
        
        # Search
        results = await self.qdrant.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=k,
            query_filter=filters
        )
        
        return [
            {
                'text': hit.payload['text'],
                'score': hit.score,
                'chunk_type': hit.payload['chunk_type'],
                'source': hit.payload.get('source_location', '')
            }
            for hit in results
        ]
    
    async def get_character_statistics(self) -> Dict:
        """Get statistics about character's indexed content"""
        collection_info = await self.qdrant.get_collection(
            collection_name=self.collection_name
        )
        
        return {
            'total_chunks': collection_info.points_count,
            'collection_name': self.collection_name,
            'character_name': self.character_name
        }
```

### **5.3 Character Agent Service**

**Purpose**: Generate character-specific dialogue and actions

**Location**: `services/character-agent/main.py`

```python
# character-agent/main.py
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import os
import time
import redis.asyncio as redis
from groq import AsyncGroq

from .rag_system import CharacterRAG
from shared.models import DialogueRequest, DialogueResponse

app = FastAPI()

# Environment configuration
CHARACTER_NAME = os.getenv("CHARACTER_NAME")
CHARACTER_ID = os.getenv("CHARACTER_ID")
QDRANT_URL = os.getenv("QDRANT_URL")
REDIS_URL = os.getenv("REDIS_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialize clients
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
rag_system = CharacterRAG(CHARACTER_ID, CHARACTER_NAME, QDRANT_URL)

# Prometheus metrics
dialogue_requests = Counter(
    'dialogue_requests_total',
    'Total dialogue generation requests',
    ['character', 'status']
)
dialogue_duration = Histogram(
    'dialogue_generation_duration_seconds',
    'Time to generate dialogue',
    ['character']
)
rag_retrieval_duration = Histogram(
    'rag_retrieval_duration_seconds',
    'Time to retrieve from RAG',
    ['character']
)

@app.on_event("startup")
async def startup():
    """Initialize on startup"""
    # Load character profile from database
    # In production, fetch from PostgreSQL
    pass

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    stats = await rag_system.get_character_statistics()
    return {
        "status": "healthy",
        "character": CHARACTER_NAME,
        "collection_size": stats['total_chunks']
    }

@app.post("/generate-dialogue", response_model=DialogueResponse)
async def generate_dialogue(request: DialogueRequest):
    """Generate dialogue for this character"""
    start_time = time.time()
    
    try:
        # 1. Check cache
        cache_key = f"dialogue:{CHARACTER_ID}:{hash(str(request.dict()))}"
        cached = await redis_client.get(cache_key)
        
        if cached:
            import json
            dialogue_requests.labels(
                character=CHARACTER_NAME,
                status='cache_hit'
            ).inc()
            return DialogueResponse(**json.loads(cached))
        
        # 2. Retrieve similar past dialogue
        retrieval_start = time.time()
        similar_examples = await rag_system.retrieve_similar_dialogue(
            query=request.scene_context.get('description', ''),
            k=5,
            chunk_type='dialogue'
        )
        rag_retrieval_duration.labels(character=CHARACTER_NAME).observe(
            time.time() - retrieval_start
        )
        
        # 3. Build prompt with retrieved examples
        examples_text = "\n".join([
            f"- {ex['text']}" for ex in similar_examples
        ])
        
        prompt = f"""You are {CHARACTER_NAME}, a character in this story.

Your personality: {request.scene_context.get('personality', 'Unknown')}

Here are examples of how {CHARACTER_NAME} speaks:
{examples_text}

Current scene: {request.beat_description}
Your emotional state: {request.emotional_state}
Other characters present: {', '.join(request.other_characters)}

Previous dialogue in this scene:
{_format_previous_dialogue(request.previous_dialogue)}

Write {CHARACTER_NAME}'s next line of dialogue. Match their unique voice and speech patterns from the examples.

Important:
- Stay in character
- Match the emotional tone
- Be natural and conversational
- Keep it concise (1-3 sentences)

{CHARACTER_NAME}:"""

        # 4. Generate with Groq
        response = await groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=200
        )
        
        dialogue = response.choices[0].message.content.strip()
        
        # 5. Generate accompanying action
        action = await _generate_action(
            request.scene_context,
            dialogue,
            request.emotional_state
        )
        
        # 6. Calculate voice consistency
        confidence = _calculate_voice_consistency(
            dialogue,
            [ex['text'] for ex in similar_examples]
        )
        
        # 7. Create response
        result = DialogueResponse(
            character=CHARACTER_NAME,
            dialogue=dialogue,
            action=action,
            confidence_score=confidence,
            retrieved_examples=[ex['text'] for ex in similar_examples[:3]]
        )
        
        # 8. Cache result
        await redis_client.set(
            cache_key,
            result.json(),
            ex=3600  # 1 hour
        )
        
        # 9. Metrics
        dialogue_requests.labels(
            character=CHARACTER_NAME,
            status='success'
        ).inc()
        
        return result
        
    except Exception as e:
        dialogue_requests.labels(
            character=CHARACTER_NAME,
            status='error'
        ).inc()
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        dialogue_duration.labels(character=CHARACTER_NAME).observe(
            time.time() - start_time
        )

async def _generate_action(
    scene_context: Dict,
    dialogue: str,
    emotional_state: str
) -> str:
    """Generate action to accompany dialogue"""
    prompt = f"""Given this dialogue by {CHARACTER_NAME}: "{dialogue}"
Emotional state: {emotional_state}
Scene: {scene_context.get('description', '')}

Write a brief action or body language for {CHARACTER_NAME} (1 sentence).

Action:"""
    
    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",  # Faster model for actions
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=50
    )
    
    return response.choices[0].message.content.strip()

def _calculate_voice_consistency(
    generated: str,
    examples: List[str]
) -> float:
    """
    Calculate how well generated dialogue matches character voice
    Simple implementation - can be enhanced
    """
    from sentence_transformers import util
    
    if not examples:
        return 0.5  # No examples to compare
    
    # Calculate semantic similarity
    gen_embedding = rag_system.embedding_model.encode(generated)
    example_embeddings = rag_system.embedding_model.encode(examples)
    
    similarities = util.cos_sim(gen_embedding, example_embeddings)
    avg_similarity = float(similarities.mean())
    
    return avg_similarity

def _format_previous_dialogue(dialogue_list: List[Dict[str, str]]) -> str:
    """Format previous dialogue for context"""
    if not dialogue_list:
        return "No previous dialogue."
    
    formatted = []
    for turn in dialogue_list[-5:]:  # Last 5 turns
        formatted.append(f"{turn['character']}: {turn['dialogue']}")
    
    return "\n".join(formatted)

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return generate_latest()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

### **5.4 Narrative Orchestrator**

**Purpose**: Coordinate multiple character agents to generate coherent scenes

**Location**: `services/orchestrator/main.py`

```python
# orchestrator/main.py
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any
import asyncio
import httpx
from groq import AsyncGroq
import os

from shared.models import (
    SceneRequest, 
    SceneGenerationState, 
    SceneBeat,
    DialogueRequest,
    DialogueResponse
)

class NarrativeOrchestrator:
    """Orchestrate scene generation across multiple character agents"""
    
    def __init__(self):
        self.groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        
        # Parse character agent URLs from environment
        agent_urls = os.getenv("CHARACTER_AGENT_URLS", "").split(",")
        self.character_agents = {}
        for url in agent_urls:
            # Extract character name from URL
            # Format: http://hermione:8002
            char_name = url.split("//")[1].split(":")[0].capitalize()
            self.character_agents[char_name] = url
        
        self.graph = self._build_graph()
    
    def _build_graph(self):
        """Build LangGraph state machine"""
        workflow = StateGraph(SceneGenerationState)
        
        # Add nodes
        workflow.add_node("plan_scene", self.plan_scene_structure)
        workflow.add_node("generate_beat", self.generate_scene_beat)
        workflow.add_node("coordinate_dialogue", self.coordinate_character_dialogue)
        workflow.add_node("add_narration", self.add_narrative_description)
        workflow.add_node("synthesize", self.synthesize_final_scene)
        
        # Define flow
        workflow.set_entry_point("plan_scene")
        workflow.add_edge("plan_scene", "generate_beat")
        workflow.add_conditional_edges(
            "generate_beat",
            self.should_continue_beats,
            {
                "continue": "coordinate_dialogue",
                "done": "synthesize"
            }
        )
        workflow.add_edge("coordinate_dialogue", "add_narration")
        workflow.add_edge("add_narration", "generate_beat")
        workflow.add_edge("synthesize", END)
        
        return workflow.compile()
    
    async def plan_scene_structure(
        self, 
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Break scene into narrative beats"""
        
        request = state['scene_request']
        
        prompt = f"""You are a narrative planner for a creative writing scene.

Scene request:
- Description: {request['scene_description']}
- Setting: {request['setting']}
- Characters: {', '.join(request['characters'])}
- Emotional tone: {request['emotional_tone']}
- POV character: {request.get('pov_character', 'third person')}

Break this scene into 3-5 narrative beats (key moments).
Each beat should:
- Advance the plot
- Involve character interaction
- Create an emotional shift

Return as JSON array:
[
  {{
    "beat_index": 0,
    "beat_description": "Characters enter the room, tension is high",
    "characters_involved": ["Character1", "Character2"],
    "emotional_shift": "nervous to confrontational",
    "plot_objective": "reveal the secret"
  }},
  ...
]

JSON:"""
        
        response = await self.groq.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1000
        )
        
        import json
        beats_data = json.loads(response.choices[0].message.content)
        beats = [SceneBeat(**beat) for beat in beats_data]
        
        state['scene_beats'] = beats
        state['current_beat_index'] = 0
        state['character_turns'] = []
        state['generated_content'] = []
        
        return state
    
    async def generate_scene_beat(
        self,
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process current beat"""
        # Just update state, actual work happens in coordinate_dialogue
        return state
    
    async def coordinate_character_dialogue(
        self,
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Coordinate dialogue between characters for current beat"""
        
        current_beat = state['scene_beats'][state['current_beat_index']]
        characters_in_beat = current_beat['characters_involved']
        
        # Generate dialogue for each character in turn
        dialogue_turns = []
        
        for char_name in characters_in_beat:
            if char_name not in self.character_agents:
                continue  # Skip if agent not available
            
            # Build context for this character
            context = {
                'description': current_beat['beat_description'],
                'setting': state['scene_request']['setting'],
                'plot_objective': current_beat.get('plot_objective', ''),
                'previous_beats': [
                    b['beat_description'] 
                    for b in state['scene_beats'][:state['current_beat_index']]
                ]
            }
            
            # Call character agent
            agent_url = self.character_agents[char_name]
            
            dialogue_request = DialogueRequest(
                character_name=char_name,
                scene_context=context,
                emotional_state=current_beat.get('emotional_shift', 'neutral'),
                other_characters=[
                    c for c in characters_in_beat if c != char_name
                ],
                beat_description=current_beat['beat_description'],
                previous_dialogue=dialogue_turns
            )
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{agent_url}/generate-dialogue",
                    json=dialogue_request.dict(),
                    timeout=30.0
                )
                response.raise_for_status()
                
                dialogue_response = DialogueResponse(**response.json())
            
            # Add to turns
            dialogue_turns.append({
                'character': char_name,
                'dialogue': dialogue_response.dialogue,
                'action': dialogue_response.action,
                'confidence': dialogue_response.confidence_score
            })
        
        # Update state
        state['character_turns'].extend(dialogue_turns)
        
        return state
    
    async def add_narrative_description(
        self,
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add narrative description around dialogue"""
        
        current_beat = state['scene_beats'][state['current_beat_index']]
        recent_turns = state['character_turns'][-len(current_beat['characters_involved']):]
        
        # Format dialogue
        dialogue_text = "\n".join([
            f"{turn['character']}: \"{turn['dialogue']}\" {turn.get('action', '')}"
            for turn in recent_turns
        ])
        
        # Generate narrative wrapper
        prompt = f"""Add narrative description to this dialogue scene.

Setting: {state['scene_request']['setting']}
Beat objective: {current_beat['beat_description']}
Emotional tone: {current_beat.get('emotional_shift', '')}

Dialogue:
{dialogue_text}

Add:
- Opening description (setting the scene)
- Brief transitions between dialogue
- Closing description

Format as natural prose. Keep descriptions concise.

Scene:"""
        
        response = await self.groq.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=500
        )
        
        beat_content = response.choices[0].message.content
        state['generated_content'].append(beat_content)
        
        # Move to next beat
        state['current_beat_index'] += 1
        
        return state
    
    def should_continue_beats(
        self,
        state: Dict[str, Any]
    ) -> str:
        """Decide if we should continue to next beat or finish"""
        if state['current_beat_index'] < len(state['scene_beats']):
            return "continue"
        else:
            return "done"
    
    async def synthesize_final_scene(
        self,
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Combine all beats into final scene"""
        
        # Join all generated content
        scene_text = "\n\n".join(state['generated_content'])
        
        # Optional: Final polish pass
        prompt = f"""Polish this scene for consistency and flow.
Fix any awkward transitions, ensure dialogue tags are clear.

Scene:
{scene_text}

Polished version:"""
        
        response = await self.groq.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=2000
        )
        
        final_scene = response.choices[0].message.content
        state['final_scene'] = final_scene
        
        # Add metadata
        state['metadata'] = {
            'total_beats': len(state['scene_beats']),
            'characters_used': list(set([
                turn['character'] for turn in state['character_turns']
            ])),
            'word_count': len(final_scene.split())
        }
        
        return state
    
    async def generate_scene(
        self,
        request: SceneRequest
    ) -> Dict[str, Any]:
        """Main entry point for scene generation"""
        
        initial_state = {
            'scene_request': request.dict(),
            'scene_beats': [],
            'current_beat_index': 0,
            'character_turns': [],
            'generated_content': [],
            'final_scene': '',
            'metadata': {}
        }
        
        # Run the graph
        final_state = await self.graph.ainvoke(initial_state)
        
        return final_state

# FastAPI app
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import json

app = FastAPI()
orchestrator = NarrativeOrchestrator()

@app.post("/orchestrate")
async def orchestrate_scene(request: SceneRequest):
    """Generate scene with streaming updates"""
    
    async def generate():
        try:
            # This is simplified - in production, use callbacks for real streaming
            result = await orchestrator.generate_scene(request)
            
            # Stream events
            yield json.dumps({
                'event': 'scene_complete',
                'data': result
            }) + '\n'
            
        except Exception as e:
            yield json.dumps({
                'event': 'error',
                'data': {'message': str(e)}
            }) + '\n'
    
    return StreamingResponse(
        generate(),
        media_type='text/event-stream'
    )

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "orchestrator"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
```

---

## **6. File Structure**

```
narrative-ai/
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
│
├── frontend/                      # Next.js frontend
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── dashboard/
│   │   │   ├── page.tsx
│   │   │   ├── manuscripts/page.tsx
│   │   │   └── scenes/page.tsx
│   │   └── generate/
│   │       └── page.tsx
│   ├── components/
│   │   ├── SceneGenerator.tsx
│   │   ├── CharacterAgentStatus.tsx
│   │   ├── ManuscriptUploader.tsx
│   │   └── ScenePreview.tsx
│   └── lib/
│       ├── api.ts
│       └── types.ts
│
├── services/
│   ├── api-gateway/              # FastAPI gateway
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── manuscripts.py
│   │   │   ├── scenes.py
│   │   │   └── characters.py
│   │   ├── dependencies/
│   │   │   ├── auth.py
│   │   │   └── database.py
│   │   └── middleware/
│   │       └── logging.py
│   │
│   ├── orchestrator/             # LangGraph orchestrator
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   └── orchestrator.py
│   │
│   ├── character-agent/          # Character agent microservice
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   ├── rag_system.py
│   │   └── voice_matcher.py
│   │
│   ├── document-parser/          # Document processing
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py
│   │   ├── parser.py
│   │   └── character_extractor.py
│   │
│   └── shared/                   # Shared code
│       ├── __init__.py
│       ├── models.py
│       ├── database.py
│       └── config.py
│
├── k8s/                          # Kubernetes manifests
│   ├── base/
│   │   ├── namespace.yaml
│   │   ├── configmap.yaml
│   │   └── secrets.yaml
│   ├── services/
│   │   ├── frontend/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── hpa.yaml
│   │   ├── api-gateway/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── hpa.yaml
│   │   ├── orchestrator/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── hpa.yaml
│   │   └── character-agent/
│   │       ├── deployment-template.yaml
│   │       ├── service-template.yaml
│   │       └── hpa.yaml
│   ├── infrastructure/
│   │   ├── qdrant/
│   │   │   ├── statefulset.yaml
│   │   │   └── service.yaml
│   │   ├── postgres/
│   │   │   ├── statefulset.yaml
│   │   │   └── service.yaml
│   │   └── redis/
│   │       ├── deployment.yaml
│   │       └── service.yaml
│   ├── monitoring/
│   │   ├── prometheus/
│   │   │   ├── deployment.yaml
│   │   │   ├── service.yaml
│   │   │   └── configmap.yaml
│   │   └── grafana/
│   │       ├── deployment.yaml
│   │       └── service.yaml
│   └── ingress/
│       └── ingress.yaml
│
├── monitoring/
│   ├── prometheus.yml
│   └── grafana-dashboards/
│       └── narrative-ai-dashboard.json
│
├── scripts/
│   ├── deploy-local.sh
│   ├── deploy-production.sh
│   ├── build-images.sh
│   ├── create-character-agent.sh
│   └── init-db.sql
│
└── tests/
    ├── unit/
    │   ├── test_rag_system.py
    │   ├── test_character_agent.py
    │   └── test_orchestrator.py
    ├── integration/
    │   ├── test_scene_generation.py
    │   └── test_api_gateway.py
    └── e2e/
        └── test_full_flow.py
```

---

## **7. Implementation Phases**

### **Phase 1: Foundation (Week 1-2)**

**Goal**: Set up core infrastructure and basic RAG

**Tasks**:
1. **Day 1-2: Project Setup**
   - [ ] Create GitHub repository
   - [ ] Set up directory structure
   - [ ] Initialize all Dockerfiles
   - [ ] Create docker-compose.yml for local dev
   - [ ] Set up .env.example with all required vars

2. **Day 3-4: Database & Models**
   - [ ] Create PostgreSQL schema (init-db.sql)
   - [ ] Implement shared Pydantic models
   - [ ] Set up SQLAlchemy models
   - [ ] Create database connection utilities

3. **Day 5-7: Document Parser**
   - [ ] Implement DocumentParser class
   - [ ] Support .docx, .pdf, .txt, .html
   - [ ] Implement CharacterExtractor with LLM
   - [ ] Create chunking strategies
   - [ ] Write unit tests

4. **Day 8-10: Basic RAG System**
   - [ ] Implement CharacterRAG class
   - [ ] Set up Qdrant integration
   - [ ] Implement embedding generation
   - [ ] Implement retrieval methods
   - [ ] Test indexing and retrieval

5. **Day 11-14: First Character Agent**
   - [ ] Create character-agent service structure
   - [ ] Implement /generate-dialogue endpoint
   - [ ] Integrate RAG retrieval
   - [ ] Integrate Groq API
   - [ ] Add caching with Redis
   - [ ] Write tests

**Deliverable**: Single character agent that can generate dialogue with RAG

---

### **Phase 2: Multi-Agent System (Week 3-4)**

**Goal**: Build orchestrator and coordinate multiple agents

**Tasks**:
1. **Day 15-17: Orchestrator Service**
   - [ ] Create orchestrator service structure
   - [ ] Implement LangGraph state machine
   - [ ] Implement plan_scene_structure node
   - [ ] Implement coordinate_character_dialogue node
   - [ ] Implement add_narration node
   - [ ] Implement synthesize_final_scene node

2. **Day 18-20: Multi-Character Setup**
   - [ ] Create script to spawn multiple character agents
   - [ ] Update docker-compose for multiple agents
   - [ ] Implement character agent discovery
   - [ ] Test orchestrator with 3 characters

3. **Day 21-24: API Gateway**
   - [ ] Create api-gateway service
   - [ ] Implement authentication (JWT)
   - [ ] Implement manuscript endpoints
   - [ ] Implement scene generation endpoint
   - [ ] Implement streaming responses
   - [ ] Add rate limiting

4. **Day 25-28: Integration Testing**
   - [ ] Test full flow: upload → process → generate
   - [ ] Test with multiple manuscripts
   - [ ] Test with different character combinations
   - [ ] Performance testing
   - [ ] Fix bugs

**Deliverable**: Working multi-agent system that generates scenes

---

### **Phase 3: Frontend & UX (Week 5-6)**

**Goal**: Build user-facing application

**Tasks**:
1. **Day 29-31: Frontend Setup**
   - [ ] Initialize Next.js project
   - [ ] Set up Tailwind CSS
   - [ ] Create layout components
   - [ ] Implement authentication pages

2. **Day 32-35: Manuscript Management UI**
   - [ ] Create manuscript upload component
   - [ ] Create manuscript list/detail pages
   - [ ] Show character extraction results
   - [ ] Display processing status

3. **Day 36-39: Scene Generator UI**
   - [ ] Create scene request form
   - [ ] Implement real-time streaming display
   - [ ] Show character agent statuses
   - [ ] Display generated scene with formatting
   - [ ] Add export functionality

4. **Day 40-42: Polish & Testing**
   - [ ] Responsive design
   - [ ] Error handling
   - [ ] Loading states
   - [ ] End-to-end testing

**Deliverable**: Functional web application

---

### **Phase 4: Kubernetes & Production (Week 7-8)**

**Goal**: Deploy on Kubernetes with full observability

**Tasks**:
1. **Day 43-45: Kubernetes Manifests**
   - [ ] Create all K8s YAML files
   - [ ] Set up namespace, configmaps, secrets
   - [ ] Create deployments for all services
   - [ ] Create StatefulSets for databases
   - [ ] Configure services and ingress

2. **Day 46-48: Monitoring & Metrics**
   - [ ] Add Prometheus metrics to all services
   - [ ] Deploy Prometheus
   - [ ] Create Grafana dashboards
   - [ ] Set up alerts

3. **Day 49-51: Auto-scaling & Optimization**
   - [ ] Configure HPAs for all services
   - [ ] Tune resource requests/limits
   - [ ] Implement pod disruption budgets
   - [ ] Test scaling behavior

4. **Day 52-56: Deployment & Testing**
   - [ ] Test on Minikube
   - [ ] Deploy to cloud (GKE/EKS)
   - [ ] Load testing
   - [ ] Security audit
   - [ ] Documentation

**Deliverable**: Production-ready Kubernetes deployment

---

## **8. Testing Strategy**

### **8.1 Unit Tests**

```python
# tests/unit/test_rag_system.py
import pytest
from services.character_agent.rag_system import CharacterRAG

@pytest.mark.asyncio
async def test_character_rag_indexing():
    """Test indexing character content"""
    rag = CharacterRAG(
        character_id="test-123",
        character_name="TestChar",
        qdrant_url="http://localhost:6333"
    )
    
    await rag.create_collection()
    
    chunks = [
        {
            'text': 'Test dialogue here',
            'chunk_type': 'dialogue',
            'source_location': 'chapter_1'
        }
    ]
    
    await rag.index_character_content(chunks)
    
    stats = await rag.get_character_statistics()
    assert stats['total_chunks'] == 1

@pytest.mark.asyncio
async def test_rag_retrieval():
    """Test retrieving similar content"""
    # Setup
    rag = CharacterRAG(
        character_id="test-123",
        character_name="TestChar",
        qdrant_url="http://localhost:6333"
    )
    
    # Test retrieval
    results = await rag.retrieve_similar_dialogue(
        query="test query",
        k=5
    )
    
    assert len(results) <= 5
    assert all('text' in r for r in results)
```

### **8.2 Integration Tests**

```python
# tests/integration/test_scene_generation.py
import pytest
import httpx
from shared.models import SceneRequest

@pytest.mark.asyncio
async def test_full_scene_generation():
    """Test complete scene generation flow"""
    
    # Create scene request
    request = SceneRequest(
        manuscript_id="test-manuscript-id",
        characters=["Hermione", "Harry", "Ron"],
        scene_description="Study session in library",
        setting="Hogwarts library",
        emotional_tone="focused but playful",
        target_word_count=500
    )
    
    # Call API
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://api-gateway:8000/api/v1/scenes/generate",
            json=request.dict(),
            timeout=60.0
        )
    
    assert response.status_code == 200
    
    # Parse streaming response
    events = []
    for line in response.text.split('\n'):
        if line:
            events.append(json.loads(line))
    
    # Verify events
    assert any(e['event_type'] == 'scene_complete' for e in events)
    
    final_event = [e for e in events if e['event_type'] == 'scene_complete'][0]
    assert 'content' in final_event['data']
    assert len(final_event['data']['content'].split()) > 100
```

### **8.3 Load Tests**

```python
# tests/performance/load_test.py
from locust import HttpUser, task, between

class NarrativeAIUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Login before tests"""
        response = self.client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "testpass"
        })
        self.token = response.json()['access_token']
    
    @task(3)
    def generate_scene(self):
        """Test scene generation"""
        self.client.post(
            "/api/v1/scenes/generate",
            json={
                "manuscript_id": "test-id",
                "characters": ["Hermione", "Harry"],
                "scene_description": "Test scene",
                "setting": "Library",
                "emotional_tone": "neutral",
                "target_word_count": 300
            },
            headers={"Authorization": f"Bearer {self.token}"}
        )
    
    @task(1)
    def list_manuscripts(self):
        """Test manuscript listing"""
        self.client.get(
            "/api/v1/manuscripts",
            headers={"Authorization": f"Bearer {self.token}"}
        )
```

---

## **9. Deployment Specifications**

### **9.1 Environment Variables**

```bash
# .env.example

# Database
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=narrativeai
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-secure-password

# Qdrant
QDRANT_URL=http://qdrant:6333

# Redis
REDIS_URL=redis://redis:6379

# LLM APIs
GROQ_API_KEY=your-groq-api-key

# JWT
SECRET_KEY=your-secret-key-min-32-chars
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Services
API_GATEWAY_URL=http://api-gateway:8000
ORCHESTRATOR_URL=http://orchestrator:8001
CHARACTER_AGENT_URLS=http://hermione:8002,http://harry:8003,http://ron:8004

# Monitoring
PROMETHEUS_URL=http://prometheus:9090
GRAFANA_URL=http://grafana:3001
```

### **9.2 Resource Requirements**

**Minimum for Development (Minikube)**:
- CPU: 4 cores
- Memory: 8GB
- Disk: 50GB

**Production (per environment)**:
- **Frontend** (2-5 pods): 200m CPU, 256Mi memory each
- **API Gateway** (2-5 pods): 500m CPU, 512Mi memory each
- **Orchestrator** (2-5 pods): 1000m CPU, 1Gi memory each
- **Character Agent** (2-10 pods per character): 500m CPU, 512Mi memory each
- **Qdrant** (1-3 replicas): 1000m CPU, 2Gi memory each
- **PostgreSQL** (1-3 replicas): 1000m CPU, 2Gi memory each
- **Redis** (1-3 replicas): 500m CPU, 512Mi memory each

**Total cluster**: 3-5 nodes, n2-standard-4 (4 vCPU, 16GB) or equivalent

---

## **10. Monitoring & Observability**

### **10.1 Key Metrics**

**Application Metrics**:
- `dialogue_requests_total{character, status}` - Counter
- `dialogue_generation_duration_seconds{character}` - Histogram
- `rag_retrieval_duration_seconds{character}` - Histogram
- `voice_consistency_score{character}` - Gauge
- `cache_hit_rate{character}` - Gauge
- `scene_generation_duration_seconds` - Histogram
- `scene_word_count` - Histogram

**Infrastructure Metrics**:
- Pod CPU/Memory usage
- Request latency (p50, p95, p99)
- Error rates
- Pod restart count

### **10.2 Grafana Dashboards**

Create dashboards for:
1. **System Overview**: All services health, request rates
2. **Character Agents**: Per-character metrics, voice consistency
3. **Performance**: Latency distributions, throughput
4. **Costs**: API usage, compute costs

### **10.3 Logging**

Use structured logging with JSON format:

```python
import logging
import json

logger = logging.getLogger(__name__)

def log_scene_generation(scene_id, duration, characters):
    logger.info(json.dumps({
        'event': 'scene_generated',
        'scene_id': scene_id,
        'duration_ms': duration,
        'characters': characters,
        'timestamp': datetime.utcnow().isoformat()
    }))
```

---

## **Next Steps for Claude Code**

**To start implementation**:

1. **Create project structure**:
   ```bash
   mkdir narrative-ai && cd narrative-ai
   # Create all directories from section 6
   ```

2. **Start with Phase 1, Day 1-2**:
   - Initialize Git repository
   - Create basic Dockerfiles
   - Set up docker-compose.yml

3. **Work through phases sequentially**, committing after each major milestone

4. **Test continuously** as you build each component

5. **Document** any deviations or improvements to this design

**Key Files to Start With**:
1. `docker-compose.yml`
2. `services/shared/models.py`
3. `scripts/init-db.sql`
4. `services/document-parser/parser.py`

Let me know when you're ready to begin, and I'll guide you through each phase!
