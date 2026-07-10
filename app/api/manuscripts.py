"""Manuscript management endpoints"""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.models import ManuscriptStatus
from app.core.orm_models import (
    Character as CharacterORM,
    Manuscript as ManuscriptORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.parsing.pipeline import (
    UploadValidationError,
    process_manuscript,
    save_upload,
)

router = APIRouter()


@router.post("/upload", response_model=dict)
async def upload_manuscript(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = "",
    author: str = "",
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manuscript; character extraction/indexing runs in the background."""
    # Extraction spends LLM quota — gate it on the per-user daily budget.
    await check_user_budget(db, current_user.id)
    if not title:
        title = file.filename or "Untitled"

    try:
        saved = await save_upload(file.filename or "upload.txt", await file.read())
    except UploadValidationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error parsing document: {e}",
        )

    # Per-user duplicate guard (content_hash is unique per user, not globally).
    existing = await db.execute(
        select(ManuscriptORM).where(
            ManuscriptORM.user_id == current_user.id,
            ManuscriptORM.content_hash == saved["content_hash"],
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already uploaded this manuscript",
        )

    manuscript = ManuscriptORM(
        user_id=current_user.id,
        title=title,
        author=author or None,
        content_hash=saved["content_hash"],
        content_text=saved["text"],
        word_count=saved["word_count"],
        status=ManuscriptStatus.PROCESSING.value,
    )
    db.add(manuscript)
    await db.commit()
    await db.refresh(manuscript)

    background_tasks.add_task(
        process_manuscript, manuscript.id, current_user.id, saved["text"]
    )

    return {
        "id": str(manuscript.id),
        "title": manuscript.title,
        "author": manuscript.author,
        "word_count": manuscript.word_count,
        "status": manuscript.status,
        "message": "Manuscript uploaded successfully. Processing started.",
    }


@router.get("/", response_model=dict)
async def list_manuscripts(
    skip: int = 0,
    limit: int = 20,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's manuscripts."""
    total = (
        await db.execute(
            select(func.count())
            .select_from(ManuscriptORM)
            .where(ManuscriptORM.user_id == current_user.id)
        )
    ).scalar_one()

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
                "processed_at": m.processed_at.isoformat() if m.processed_at else None,
            }
            for m in manuscripts
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


async def _owned_manuscript(
    manuscript_id: UUID, current_user: UserORM, db: AsyncSession
) -> ManuscriptORM:
    result = await db.execute(
        select(ManuscriptORM).where(
            ManuscriptORM.id == manuscript_id,
            ManuscriptORM.user_id == current_user.id,
        )
    )
    manuscript = result.scalar_one_or_none()
    if not manuscript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Manuscript not found"
        )
    return manuscript


@router.get("/{manuscript_id}", response_model=dict)
async def get_manuscript(
    manuscript_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get manuscript details."""
    manuscript = await _owned_manuscript(manuscript_id, current_user, db)
    return {
        "id": str(manuscript.id),
        "title": manuscript.title,
        "author": manuscript.author,
        "word_count": manuscript.word_count,
        "status": manuscript.status,
        "uploaded_at": (
            manuscript.uploaded_at.isoformat() if manuscript.uploaded_at else None
        ),
        "processed_at": (
            manuscript.processed_at.isoformat() if manuscript.processed_at else None
        ),
    }


@router.get("/{manuscript_id}/characters", response_model=dict)
async def get_manuscript_characters(
    manuscript_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List characters extracted from a manuscript."""
    await _owned_manuscript(manuscript_id, current_user, db)

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
                "indexed_at": c.indexed_at.isoformat() if c.indexed_at else None,
            }
            for c in characters
        ],
    }


@router.delete("/{manuscript_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_manuscript(
    manuscript_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a manuscript, its characters, and their indexed voices."""
    from app.rag.store import get_chunk_store

    manuscript = await _owned_manuscript(manuscript_id, current_user, db)
    characters = (
        (
            await db.execute(
                select(CharacterORM).where(CharacterORM.manuscript_id == manuscript_id)
            )
        )
        .scalars()
        .all()
    )
    store = get_chunk_store()
    for character in characters:
        try:
            await store.delete_character(str(character.id))
        except Exception:
            pass  # vector cleanup is best-effort; rows cascade below
    await db.delete(manuscript)
    await db.commit()
    return None


@router.post("/{manuscript_id}/process", response_model=dict)
async def reprocess_manuscript(
    manuscript_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run character extraction/indexing (e.g. after a failed run)."""
    manuscript = await _owned_manuscript(manuscript_id, current_user, db)
    if not manuscript.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manuscript has no stored content to process",
        )
    await check_user_budget(db, current_user.id)
    manuscript.status = ManuscriptStatus.PROCESSING.value
    await db.commit()
    background_tasks.add_task(process_manuscript, manuscript.id, current_user.id)
    return {"id": str(manuscript.id), "status": manuscript.status}
