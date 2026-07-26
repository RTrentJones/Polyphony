"""Scene generation endpoints.

The API layer creates the Scene row ONCE, with the requesting user's id; the
background workflow only ever updates it. (The old gateway/orchestrator seam
inserted the row twice with the same PK and without user_id — both fixed
structurally here.)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.models import SceneRequest
from app.core.orm_models import (
    Scene as SceneORM,
    Source as SourceORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.jobs import repository as jobs_repo

router = APIRouter()


@router.post("/generate", response_model=dict)
async def generate_scene(
    scene_request: SceneRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Start scene generation; poll GET /api/v1/scenes/{scene_id} for the result."""
    source = (
        await db.execute(
            select(SourceORM).where(
                SourceORM.id == scene_request.source_id,
                SourceORM.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found or access denied",
        )

    await check_user_budget(db, current_user.id)

    request_dict = scene_request.model_dump(mode="json")
    # The workflow resolves the cast by book (book is the root); carry the
    # source's book_id through the job payload so it never has to re-derive it.
    request_dict["book_id"] = str(source.book_id)
    scene = SceneORM(
        user_id=current_user.id,
        source_id=scene_request.source_id,
        setting=scene_request.setting,
        emotional_tone=scene_request.emotional_tone,
        characters=scene_request.characters,
        scene_description=scene_request.scene_description,
        scene_request=request_dict,
        status="processing",
    )
    db.add(scene)
    await db.flush()
    # The job commits atomically with the scene row: no scene can exist in
    # 'processing' without a durable job that will drive it to a terminal state.
    await jobs_repo.enqueue(
        db,
        kind="generate_scene",
        payload={
            "scene_id": str(scene.id),
            "request": request_dict,
            "user_id": str(current_user.id),
        },
        user_id=current_user.id,
        max_attempts=1,  # a retry re-spends LLM budget; user can re-trigger
    )
    await db.commit()
    await db.refresh(scene)

    return {
        "scene_id": str(scene.id),
        "status": scene.status,
        "message": (
            "Scene generation started. Use GET /api/v1/scenes/{scene_id} "
            "to check status and retrieve the generated scene."
        ),
    }


@router.get("/", response_model=dict)
async def list_scenes(
    source_id: UUID | None = None,
    skip: int = Query(0, ge=0, le=1000),
    limit: int = Query(20, ge=1, le=100),
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's generated scenes."""
    query = select(SceneORM).where(SceneORM.user_id == current_user.id)
    count_query = (
        select(func.count())
        .select_from(SceneORM)
        .where(SceneORM.user_id == current_user.id)
    )
    if source_id:
        query = query.where(SceneORM.source_id == source_id)
        count_query = count_query.where(SceneORM.source_id == source_id)

    query = query.order_by(SceneORM.created_at.desc()).offset(skip).limit(limit)
    scenes = (await db.execute(query)).scalars().all()
    total = (await db.execute(count_query)).scalar_one()

    return {
        "scenes": [
            {
                "id": str(s.id),
                "source_id": str(s.source_id) if s.source_id else None,
                "characters": s.characters,
                "status": s.status,
                "preview": (
                    (s.generated_content[:200] + "...")
                    if s.generated_content and len(s.generated_content) > 200
                    else s.generated_content
                ),
                "generation_time_ms": s.generation_time_ms,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenes
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{scene_id}", response_model=dict)
async def get_scene(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full scene details."""
    result = await db.execute(
        select(SceneORM).where(
            SceneORM.id == scene_id, SceneORM.user_id == current_user.id
        )
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )

    return {
        "id": str(scene.id),
        "source_id": str(scene.source_id) if scene.source_id else None,
        "status": scene.status,
        "content": scene.generated_content,
        "characters": scene.characters,
        "scene_request": scene.scene_request,
        "word_count": scene.word_count,
        "generation_time_ms": scene.generation_time_ms,
        "evaluation_scores": scene.evaluation_scores,
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
    }


@router.delete("/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scene."""
    result = await db.execute(
        select(SceneORM).where(
            SceneORM.id == scene_id, SceneORM.user_id == current_user.id
        )
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )

    await db.delete(scene)
    await db.commit()
    return None
