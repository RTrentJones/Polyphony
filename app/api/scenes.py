"""Scene generation endpoints.

The API layer creates the Scene row ONCE, with the requesting user's id; the
background workflow only ever updates it. (The old gateway/orchestrator seam
inserted the row twice with the same PK and without user_id — both fixed
structurally here.)
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.api.books import owned_scene
from app.core.orm_models import (
    Book as BookORM,
    Scene as SceneORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.versioning import trash

router = APIRouter()

# Scenes are generated ONLY inside a book, into a chapter
# (POST /books/chapters/{chapter_id}/scenes/generate). The old standalone
# `POST /scenes/generate` created chapter-less scenes and is deliberately gone:
# the book is the root, so nothing loose is generable. These endpoints only READ,
# list, and delete scenes.
#
# Scenes are book-rooted (SPEC-04): every read here authorises through the book
# (`Scene.book_id -> Book.user_id`), keeping `Scene.user_id` as the tenant guard.
# The list stays (the deployed verify gate asserts it 401s unauthenticated) but
# shows only scenes reachable through the user's books; `GET/DELETE /{id}` stay
# because the eval harness and the frontend poll them.


@router.get("/", response_model=dict)
async def list_scenes(
    source_id: UUID | None = None,
    skip: int = Query(0, ge=0, le=1000),
    limit: int = Query(20, ge=1, le=100),
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the scenes of the current user's books (never a book-less orphan)."""
    on_book = SceneORM.book_id == BookORM.id
    owner = (
        BookORM.user_id == current_user.id,
        SceneORM.user_id == current_user.id,
        SceneORM.deleted_at.is_(None),
    )
    query = select(SceneORM).join(BookORM, on_book).where(*owner)
    count_query = (
        select(func.count()).select_from(SceneORM).join(BookORM, on_book).where(*owner)
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
                "book_id": str(s.book_id),
                "chapter_id": str(s.chapter_id) if s.chapter_id else None,
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
    scene = await owned_scene(scene_id, current_user, db)
    return {
        "id": str(scene.id),
        "book_id": str(scene.book_id),
        "chapter_id": str(scene.chapter_id) if scene.chapter_id else None,
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
    """Move a scene to its book's Trash (SPEC-13), its prose and revision
    history kept in entity_versions first (SPEC-04)."""
    scene = await owned_scene(scene_id, current_user, db)
    await trash.soft_delete(db, "scene", scene, created_by=current_user.id)
    await db.commit()
    return None
