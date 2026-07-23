"""Character bible endpoints: CRUD, voice seeding, voice testing."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
from datetime import datetime, timezone

from app.characters.dialogue import generate_dialogue
from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    Character as CharacterORM,
    CharacterChunk as CharacterChunkORM,
    Source as SourceORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.rag.store import get_chunk_store

router = APIRouter()


class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # Book is the root: every character belongs to exactly one book
    # (docs/ADR-002-book-as-root.md §1). `source_id` is optional provenance —
    # which uploaded/pasted Source this character was extracted from, if any.
    book_id: UUID
    source_id: Optional[UUID] = None
    description: Optional[str] = None
    personality_traits: dict = Field(default_factory=dict)
    voice_characteristics: dict = Field(default_factory=dict)
    role: Optional[str] = Field(None, max_length=100)
    goals: Optional[str] = None
    arc: Optional[str] = None
    notes: Optional[str] = None


class CharacterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    personality_traits: Optional[dict] = None
    voice_characteristics: Optional[dict] = None
    role: Optional[str] = Field(None, max_length=100)
    goals: Optional[str] = None
    arc: Optional[str] = None
    notes: Optional[str] = None


class VoiceSamples(BaseModel):
    samples: list[str] = Field(..., min_length=1, max_length=500)
    chunk_type: str = "dialogue"


class VoiceTest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=1000)
    context: Optional[str] = None


def _owned_select(current_user: UserORM):
    # user_id is NOT NULL (backfilled by migration 0005), so direct ownership
    # is the whole story — no legacy via-manuscript fallback needed.
    return select(CharacterORM).where(CharacterORM.user_id == current_user.id)


async def _owned_character(
    character_id: UUID, current_user: UserORM, db: AsyncSession
) -> CharacterORM:
    """A character the current user owns."""
    result = await db.execute(
        _owned_select(current_user).where(CharacterORM.id == character_id)
    )
    character = result.scalar_one_or_none()
    if not character:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Character not found"
        )
    return character


@router.get("/", response_model=dict)
async def list_characters(
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """All of the current user's characters (extracted and manual)."""
    result = await db.execute(_owned_select(current_user).order_by(CharacterORM.name))
    characters = result.scalars().all()
    return {
        "characters": [
            {
                "id": str(c.id),
                "name": c.name,
                "description": c.description,
                "role": c.role,
                "book_id": str(c.book_id),
                "source_id": str(c.source_id) if c.source_id else None,
                "dialogue_count": c.dialogue_count,
                "indexed_at": c.indexed_at.isoformat() if c.indexed_at else None,
            }
            for c in characters
        ]
    }


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_character(
    payload: CharacterCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a character in a book (manual authoring, no source required)."""
    # The book is the parent — verify the caller owns it.
    book = (
        await db.execute(
            select(BookORM).where(
                BookORM.id == payload.book_id,
                BookORM.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    # If a provenance Source is named, it must belong to the same book.
    if payload.source_id is not None:
        source = (
            await db.execute(
                select(SourceORM).where(
                    SourceORM.id == payload.source_id,
                    SourceORM.book_id == payload.book_id,
                    SourceORM.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source not found in this book",
            )

    character = CharacterORM(
        user_id=current_user.id,
        book_id=payload.book_id,
        source_id=payload.source_id,
        name=payload.name,
        description=payload.description,
        personality_traits=payload.personality_traits,
        voice_characteristics=payload.voice_characteristics,
        role=payload.role,
        goals=payload.goals,
        arc=payload.arc,
        notes=payload.notes,
    )
    db.add(character)
    try:
        await db.commit()
    except IntegrityError:
        # uq_characters_book_name: a character name is unique within a book.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A character with this name already exists in this book",
        )
    await db.refresh(character)
    return {"id": str(character.id), "name": character.name}


@router.get("/{character_id}", response_model=dict)
async def get_character(
    character_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Character profile + indexed-voice statistics."""
    character = await _owned_character(character_id, current_user, db)
    stats = await get_chunk_store().character_statistics(str(character.id))
    return {
        "id": str(character.id),
        "book_id": str(character.book_id),
        "source_id": (str(character.source_id) if character.source_id else None),
        "name": character.name,
        "description": character.description,
        "personality_traits": character.personality_traits or {},
        "voice_characteristics": character.voice_characteristics or {},
        "role": character.role,
        "goals": character.goals,
        "arc": character.arc,
        "notes": character.notes,
        "dialogue_count": character.dialogue_count,
        "indexed_at": (
            character.indexed_at.isoformat() if character.indexed_at else None
        ),
        "voice_stats": stats,
    }


@router.patch("/{character_id}", response_model=dict)
async def update_character(
    character_id: UUID,
    payload: CharacterUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a character's profile."""
    character = await _owned_character(character_id, current_user, db)
    for field_name in (
        "name",
        "description",
        "personality_traits",
        "voice_characteristics",
        "role",
        "goals",
        "arc",
        "notes",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(character, field_name, value)
    await db.commit()
    return {"id": str(character.id), "name": character.name}


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    character_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a character and its indexed voice."""
    character = await _owned_character(character_id, current_user, db)
    await get_chunk_store().delete_character(str(character.id))
    await db.delete(character)
    await db.commit()
    return None


@router.post("/{character_id}/voice-samples", response_model=dict)
async def add_voice_samples(
    character_id: UUID,
    payload: VoiceSamples,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Seed/extend a character's voice with pasted sample text."""
    character = await _owned_character(character_id, current_user, db)
    chunks = [
        {
            "text": s.strip(),
            "chunk_type": payload.chunk_type,
            "source_location": "manual",
        }
        for s in payload.samples
        if s.strip()
    ]
    indexed = await get_chunk_store().index_chunks(
        character_id=str(character.id),
        character_name=character.name,
        user_id=str(current_user.id),
        chunks=chunks,
    )
    for chunk in chunks:
        db.add(
            CharacterChunkORM(
                character_id=character.id,
                chunk_type=chunk["chunk_type"],
                content=chunk["text"],
                source_location="manual",
            )
        )
    if indexed:
        character.indexed_at = datetime.now(timezone.utc)
        if payload.chunk_type == "dialogue":
            character.dialogue_count = (character.dialogue_count or 0) + indexed
    await db.commit()
    return {"id": str(character.id), "indexed": indexed}


@router.post("/{character_id}/test-dialogue", response_model=dict)
async def test_dialogue(
    character_id: UUID,
    payload: VoiceTest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Test a character's voice before using them in scenes."""
    character = await _owned_character(character_id, current_user, db)
    response = await generate_dialogue(
        character_id=str(character.id),
        character_name=character.name,
        beat_description=payload.prompt,
        scene_context={"description": payload.context or payload.prompt},
        emotional_state="neutral",
        other_characters=[],
        user_id=current_user.id,
    )
    return response
