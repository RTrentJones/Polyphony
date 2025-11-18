"""Scene generation endpoints"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import httpx
from uuid import UUID

from services.shared.database import get_db
from services.shared.models import SceneRequest, StreamEvent
from services.shared.orm_models import User as UserORM, Scene as SceneORM, Manuscript as ManuscriptORM
from services.shared.auth import get_current_active_user
from services.shared.config import settings


router = APIRouter()


@router.post("/generate", response_model=dict)
async def generate_scene(
    scene_request: SceneRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a scene using multi-agent orchestration

    Args:
        scene_request: Scene generation request with characters, setting, etc.
        current_user: Current authenticated user
        db: Database session

    Returns:
        Generated scene with metadata
    """
    # Verify manuscript exists and user owns it
    manuscript_result = await db.execute(
        select(ManuscriptORM).where(
            ManuscriptORM.id == scene_request.manuscript_id,
            ManuscriptORM.user_id == current_user.id
        )
    )
    manuscript = manuscript_result.scalar_one_or_none()

    if not manuscript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manuscript not found or access denied"
        )

    try:
        # Call orchestrator service
        # The orchestrator creates the scene record and runs the workflow in background
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.ORCHESTRATOR_URL}/orchestrate",
                json=scene_request.dict(),
                timeout=30.0  # Just starting the workflow, not waiting for completion
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Orchestrator error: {response.text}"
                )

            orchestration_result = response.json()

        return {
            "scene_id": orchestration_result["scene_id"],
            "status": orchestration_result["status"],
            "message": "Scene generation started. Use GET /api/v1/scenes/{scene_id} to check status and retrieve the generated scene."
        }

    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error communicating with orchestrator: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating scene: {str(e)}"
        )


@router.get("/", response_model=dict)
async def list_scenes(
    manuscript_id: UUID = None,
    skip: int = 0,
    limit: int = 20,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List user's generated scenes

    Args:
        manuscript_id: Optional filter by manuscript
        skip: Number of records to skip
        limit: Maximum number of records to return
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of scenes
    """
    # Build query
    query = select(SceneORM).where(SceneORM.user_id == current_user.id)

    if manuscript_id:
        query = query.where(SceneORM.manuscript_id == manuscript_id)

    query = query.order_by(SceneORM.created_at.desc()).offset(skip).limit(limit)

    # Execute query
    result = await db.execute(query)
    scenes = result.scalars().all()

    # Get total count
    count_query = select(SceneORM).where(SceneORM.user_id == current_user.id)
    if manuscript_id:
        count_query = count_query.where(SceneORM.manuscript_id == manuscript_id)

    count_result = await db.execute(count_query)
    total = len(count_result.all())

    return {
        "scenes": [
            {
                "id": str(s.id),
                "manuscript_id": str(s.manuscript_id) if s.manuscript_id else None,
                "characters": s.characters_involved,
                "preview": s.generated_content[:200] + "..." if len(s.generated_content) > 200 else s.generated_content,
                "generation_time_ms": s.generation_time_ms,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in scenes
        ],
        "total": total,
        "skip": skip,
        "limit": limit
    }


@router.get("/{scene_id}", response_model=dict)
async def get_scene(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get full scene details

    Args:
        scene_id: Scene ID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Full scene content and metadata
    """
    result = await db.execute(
        select(SceneORM).where(
            SceneORM.id == scene_id,
            SceneORM.user_id == current_user.id
        )
    )
    scene = result.scalar_one_or_none()

    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found"
        )

    return {
        "id": str(scene.id),
        "manuscript_id": str(scene.manuscript_id) if scene.manuscript_id else None,
        "content": scene.generated_content,
        "characters": scene.characters_involved,
        "scene_request": scene.scene_request,
        "generation_time_ms": scene.generation_time_ms,
        "evaluation_scores": scene.evaluation_scores,
        "created_at": scene.created_at.isoformat() if scene.created_at else None
    }


@router.delete("/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a scene

    Args:
        scene_id: Scene ID
        current_user: Current authenticated user
        db: Database session
    """
    result = await db.execute(
        select(SceneORM).where(
            SceneORM.id == scene_id,
            SceneORM.user_id == current_user.id
        )
    )
    scene = result.scalar_one_or_none()

    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found"
        )

    await db.delete(scene)
    await db.commit()

    return None
