"""Manuscript management endpoints"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import httpx
from uuid import UUID

from services.shared.database import get_db
from services.shared.models import Manuscript, ManuscriptCreate, ManuscriptStatus
from services.shared.orm_models import User as UserORM, Manuscript as ManuscriptORM, Character as CharacterORM
from services.shared.auth import get_current_active_user
from services.shared.config import settings


router = APIRouter()


async def process_manuscript_background(
    manuscript_id: UUID,
    file_id: str,
    user_id: UUID
):
    """
    Background task to process manuscript:
    1. Extract characters using document parser
    2. Create Character records in database
    3. Index character content in RAG system
    """
    try:
        async with httpx.AsyncClient() as client:
            # Extract characters from document
            response = await client.post(
                f"{settings.API_GATEWAY_URL.replace('api-gateway', 'document-parser')}/extract-character-content",
                params={"file_id": file_id, "extract_characters": True},
                timeout=300.0
            )

            if response.status_code != 200:
                print(f"Error extracting characters: {response.text}")
                return

            data = response.json()
            characters = data.get("characters", [])

            # TODO: Create Character records and index in RAG
            # This requires database access which needs to be handled properly
            # in a background task (separate session)

            print(f"Extracted {len(characters)} characters from manuscript {manuscript_id}")

    except Exception as e:
        print(f"Error processing manuscript {manuscript_id}: {e}")


@router.post("/upload", response_model=dict)
async def upload_manuscript(
    file: UploadFile = File(...),
    title: str = "",
    author: str = "",
    background_tasks: BackgroundTasks = None,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a new manuscript

    Args:
        file: Manuscript file (.txt, .docx, .pdf, .html)
        title: Manuscript title
        author: Author name
        current_user: Current authenticated user
        db: Database session

    Returns:
        Manuscript information and processing status
    """
    # Use filename as title if not provided
    if not title:
        title = file.filename

    try:
        # Upload to document parser service
        async with httpx.AsyncClient() as client:
            files = {"file": (file.filename, await file.read(), file.content_type)}
            data = {"extract_characters": "true"}

            response = await client.post(
                f"{settings.API_GATEWAY_URL.replace('api-gateway', 'document-parser')}/parse",
                files=files,
                data=data,
                timeout=60.0
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Error parsing document: {response.text}"
                )

            parse_result = response.json()

        # Create manuscript record
        manuscript = ManuscriptORM(
            user_id=current_user.id,
            title=title,
            author=author or None,
            content_hash=parse_result.get("content_hash"),
            file_path=parse_result.get("file_path"),
            word_count=parse_result.get("word_count"),
            status=ManuscriptStatus.PROCESSING.value
        )

        db.add(manuscript)
        await db.commit()
        await db.refresh(manuscript)

        # Schedule background processing
        file_id = parse_result.get("file_id")
        if background_tasks:
            background_tasks.add_task(
                process_manuscript_background,
                manuscript.id,
                file_id,
                current_user.id
            )

        return {
            "id": str(manuscript.id),
            "title": manuscript.title,
            "author": manuscript.author,
            "word_count": manuscript.word_count,
            "status": manuscript.status,
            "characters": parse_result.get("characters", []),
            "message": "Manuscript uploaded successfully. Processing started."
        }

    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error communicating with document parser: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading manuscript: {str(e)}"
        )


@router.get("/", response_model=dict)
async def list_manuscripts(
    skip: int = 0,
    limit: int = 20,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List user's manuscripts

    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of manuscripts
    """
    # Get total count
    count_result = await db.execute(
        select(ManuscriptORM).where(ManuscriptORM.user_id == current_user.id)
    )
    total = len(count_result.all())

    # Get manuscripts
    result = await db.execute(
        select(ManuscriptORM)
        .where(ManuscriptORM.user_id == current_user.id)
        .order_by(ManuscriptORM.uploaded_at.desc())
        .offset(skip)
        .limit(limit)
    )
    manuscripts = result.scalars().all()

    return {
        "manuscripts": [
            {
                "id": str(m.id),
                "title": m.title,
                "author": m.author,
                "word_count": m.word_count,
                "status": m.status,
                "uploaded_at": m.uploaded_at.isoformat() if m.uploaded_at else None,
                "processed_at": m.processed_at.isoformat() if m.processed_at else None
            }
            for m in manuscripts
        ],
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.get("/{manuscript_id}", response_model=dict)
async def get_manuscript(
    manuscript_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get manuscript details

    Args:
        manuscript_id: Manuscript ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Manuscript details
    """
    result = await db.execute(
        select(ManuscriptORM)
        .where(
            ManuscriptORM.id == manuscript_id,
            ManuscriptORM.user_id == current_user.id
        )
    )
    manuscript = result.scalar_one_or_none()

    if not manuscript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    return {
        "id": str(manuscript.id),
        "title": manuscript.title,
        "author": manuscript.author,
        "word_count": manuscript.word_count,
        "status": manuscript.status,
        "uploaded_at": manuscript.uploaded_at.isoformat() if manuscript.uploaded_at else None,
        "processed_at": manuscript.processed_at.isoformat() if manuscript.processed_at else None
    }


@router.get("/{manuscript_id}/characters", response_model=dict)
async def get_manuscript_characters(
    manuscript_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get characters in manuscript

    Args:
        manuscript_id: Manuscript ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of characters
    """
    # Verify manuscript ownership
    manuscript_result = await db.execute(
        select(ManuscriptORM)
        .where(
            ManuscriptORM.id == manuscript_id,
            ManuscriptORM.user_id == current_user.id
        )
    )
    manuscript = manuscript_result.scalar_one_or_none()

    if not manuscript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found"
        )

    # Get characters
    characters_result = await db.execute(
        select(CharacterORM).where(CharacterORM.manuscript_id == manuscript_id)
    )
    characters = characters_result.scalars().all()

    return {
        "manuscript_id": str(manuscript_id),
        "characters": [
            {
                "id": str(c.id),
                "name": c.name,
                "description": c.description,
                "dialogue_count": c.dialogue_count,
                "indexed_at": c.indexed_at.isoformat() if c.indexed_at else None
            }
            for c in characters
        ]
    }
