"""Scene generation endpoints.

The API layer creates the Scene row ONCE, with the requesting user's id; the
background workflow only ever updates it. (The old gateway/orchestrator seam
inserted the row twice with the same PK and without user_id — both fixed
structurally here.)
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.core.database import get_db
from app.api.books import book_order, owned_scene
from app.core.orm_models import (
    Book as BookORM,
    Chapter as ChapterORM,
    Scene as SceneORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.versioning import trash
from app.versioning.trash import live

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


def _preview(text: str) -> str | None:
    return text[:200] + "..." if len(text) > 200 else (text or None)


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
                "preview": _preview(s.draft),
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
    """Get full scene details, with the beats the scene was written in
    (`beats`: [{beat_number, description, content}] by beat_number; [] for a
    scene without beats), its chapter and its neighbours in book order (SPEC-37;
    an unfiled scene has neither)."""
    scene = await owned_scene(scene_id, current_user, db, selectinload(SceneORM.beats))
    chapter = await db.get(ChapterORM, scene.chapter_id) if scene.chapter_id else None
    if chapter is not None and chapter.deleted_at is not None:
        chapter = None  # a trashed chapter leaves its scene unfiled (book_order)
    prev = nxt = None
    if chapter is not None:
        filed = book_order(live(select(SceneORM.id), SceneORM)).where(
            SceneORM.book_id == scene.book_id, ChapterORM.id.is_not(None)
        )
        ids = [None, *(await db.execute(filed)).scalars(), None]
        at = ids.index(scene.id)
        prev, nxt = ids[at - 1], ids[at + 1]
    return {
        "id": str(scene.id),
        "title": scene.title,
        "chapter": chapter
        and {"id": str(chapter.id), "number": chapter.number, "title": chapter.title},
        "neighbours": {"prev": prev and str(prev), "next": nxt and str(nxt)},
        "book_id": str(scene.book_id),
        "chapter_id": str(scene.chapter_id) if scene.chapter_id else None,
        "source_id": str(scene.source_id) if scene.source_id else None,
        "status": scene.status,
        # The draft (Scene.draft): the editor loads this, so the author's
        # saved edit wins and reloading never shows the stale generation.
        "content": scene.draft or None,
        "characters": scene.characters,
        "scene_request": scene.scene_request,
        "word_count": scene.word_count,
        "generation_time_ms": scene.generation_time_ms,
        "evaluation_scores": scene.evaluation_scores,
        "name_warnings": (scene.evaluation_scores or {}).get("name_warnings", []),
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
        "beats": [
            {
                "beat_number": b.beat_number,
                "description": b.description,
                "content": b.content,
            }
            for b in sorted(scene.beats, key=lambda b: b.beat_number)
        ],
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
