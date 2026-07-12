"""Book, chapter, and draft endpoints: the book-writing workflow."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    Chapter as ChapterORM,
    Manuscript as ManuscriptORM,
    Scene as SceneORM,
    SceneRevision as SceneRevisionORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.exports.builder import (
    CONTENT_TYPES,
    BookExport,
    ChapterExport,
    SceneExport,
    scene_text,
    to_docx,
    to_epub,
    to_markdown,
)
from app.orchestration.runner import run_prose_scene_in_background

router = APIRouter()


# --- Payloads -----------------------------------------------------------------


class BookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    author: Optional[str] = None
    synopsis: Optional[str] = None
    genre: Optional[str] = None


class BookUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    author: Optional[str] = None
    synopsis: Optional[str] = None
    genre: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(drafting|revising|complete)$")


class ChapterCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: Optional[str] = None
    position: Optional[int] = None  # append when omitted


class ChapterUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    summary: Optional[str] = None
    status: Optional[str] = None


class PositionUpdate(BaseModel):
    position: int = Field(..., ge=0)


class ChapterSceneRequest(BaseModel):
    manuscript_id: Optional[UUID] = None  # source of the character bible
    characters: list[str] = Field(..., min_length=1)
    scene_description: str = Field(..., min_length=10)
    setting: str
    emotional_tone: str
    pov_character: Optional[str] = None
    target_word_count: int = Field(default=800, ge=100, le=3000)
    style_notes: Optional[str] = None


class SceneContentUpdate(BaseModel):
    content: str = Field(..., min_length=1)


class ManualSceneCreate(BaseModel):
    """Create a scene WITHOUT LLM generation — a blank/manual draft to write in."""

    title: Optional[str] = None
    content: str = ""
    characters: list[str] = Field(default_factory=list)
    setting: str = ""
    emotional_tone: str = ""


# --- Helpers -------------------------------------------------------------------


async def _owned_book(book_id: UUID, user: UserORM, db: AsyncSession) -> BookORM:
    book = (
        await db.execute(
            select(BookORM).where(BookORM.id == book_id, BookORM.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


async def _owned_chapter(
    chapter_id: UUID, user: UserORM, db: AsyncSession
) -> ChapterORM:
    chapter = (
        await db.execute(
            select(ChapterORM)
            .join(BookORM, ChapterORM.book_id == BookORM.id)
            .where(ChapterORM.id == chapter_id, BookORM.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not chapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found"
        )
    return chapter


def _chapter_dict(c: ChapterORM) -> dict:
    return {
        "id": str(c.id),
        "book_id": str(c.book_id),
        "position": c.position,
        "title": c.title,
        "summary": c.summary,
        "status": c.status,
    }


# --- Books ----------------------------------------------------------------------


@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_book(
    payload: BookCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = BookORM(
        user_id=current_user.id,
        title=payload.title,
        author=payload.author,
        synopsis=payload.synopsis,
        genre=payload.genre,
    )
    db.add(book)
    await db.commit()
    await db.refresh(book)
    return {"id": str(book.id), "title": book.title, "status": book.status}


@router.get("/", response_model=dict)
async def list_books(
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    books = (
        (
            await db.execute(
                select(BookORM)
                .where(BookORM.user_id == current_user.id)
                .order_by(BookORM.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "books": [
            {
                "id": str(b.id),
                "title": b.title,
                "author": b.author,
                "genre": b.genre,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in books
        ]
    }


@router.get("/{book_id}", response_model=dict)
async def get_book(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    chapters = (
        (
            await db.execute(
                select(ChapterORM)
                .where(ChapterORM.book_id == book.id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": str(book.id),
        "title": book.title,
        "author": book.author,
        "synopsis": book.synopsis,
        "genre": book.genre,
        "status": book.status,
        "chapters": [_chapter_dict(c) for c in chapters],
    }


@router.patch("/{book_id}", response_model=dict)
async def update_book(
    book_id: UUID,
    payload: BookUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    for field_name in ("title", "author", "synopsis", "genre", "status"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(book, field_name, value)
    await db.commit()
    return {"id": str(book.id), "title": book.title, "status": book.status}


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    await db.delete(book)
    await db.commit()
    return None


# --- Chapters --------------------------------------------------------------------


@router.post(
    "/{book_id}/chapters", response_model=dict, status_code=status.HTTP_201_CREATED
)
async def create_chapter(
    book_id: UUID,
    payload: ChapterCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    if payload.position is None:
        max_pos = (
            await db.execute(
                select(func.coalesce(func.max(ChapterORM.position), -1)).where(
                    ChapterORM.book_id == book.id
                )
            )
        ).scalar_one()
        position = max_pos + 1
    else:
        position = payload.position
    chapter = ChapterORM(
        book_id=book.id,
        title=payload.title,
        summary=payload.summary,
        position=position,
    )
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    return _chapter_dict(chapter)


@router.get("/chapters/{chapter_id}", response_model=dict)
async def get_chapter(
    chapter_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    scenes = (
        (
            await db.execute(
                select(SceneORM)
                .where(SceneORM.chapter_id == chapter.id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )
    return {
        **_chapter_dict(chapter),
        "scenes": [
            {
                "id": str(s.id),
                "position": s.position,
                "status": s.status,
                "word_count": s.word_count,
                "preview": (scene_text(s)[:200] or None),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in scenes
        ],
    }


@router.patch("/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    chapter_id: UUID,
    payload: ChapterUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    for field_name in ("title", "summary", "status"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(chapter, field_name, value)
    await db.commit()
    return _chapter_dict(chapter)


@router.patch("/chapters/{chapter_id}/position", response_model=dict)
async def move_chapter(
    chapter_id: UUID,
    payload: PositionUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder: shift siblings between old and new positions."""
    chapter = await _owned_chapter(chapter_id, current_user, db)
    siblings = (
        (
            await db.execute(
                select(ChapterORM)
                .where(ChapterORM.book_id == chapter.book_id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )
    ordered = [c for c in siblings if c.id != chapter.id]
    new_pos = min(payload.position, len(ordered))
    ordered.insert(new_pos, chapter)
    for idx, c in enumerate(ordered):
        c.position = idx
    await db.commit()
    return _chapter_dict(chapter)


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chapter(
    chapter_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _owned_chapter(chapter_id, current_user, db)
    await db.delete(chapter)
    await db.commit()
    return None


# --- Generate into a chapter ------------------------------------------------------


@router.post("/chapters/{chapter_id}/scenes/generate", response_model=dict)
async def generate_scene_into_chapter(
    chapter_id: UUID,
    payload: ChapterSceneRequest,
    background_tasks: BackgroundTasks,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Prose-mode generation (one LLM call per beat) landing in the chapter."""
    chapter = await _owned_chapter(chapter_id, current_user, db)

    if payload.manuscript_id:
        owned = (
            await db.execute(
                select(ManuscriptORM).where(
                    ManuscriptORM.id == payload.manuscript_id,
                    ManuscriptORM.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Manuscript not found"
            )

    await check_user_budget(db, current_user.id)

    next_pos = (
        await db.execute(
            select(func.coalesce(func.max(SceneORM.position), -1)).where(
                SceneORM.chapter_id == chapter.id
            )
        )
    ).scalar_one() + 1

    # Previous scene's tail for narrative continuity
    prev_scene = (
        await db.execute(
            select(SceneORM)
            .where(SceneORM.chapter_id == chapter.id, SceneORM.status == "completed")
            .order_by(SceneORM.position.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    prior_tail = ""
    if prev_scene is not None:
        prior_tail = " ".join(scene_text(prev_scene).split()[-500:])

    request_dict = payload.model_dump(mode="json")
    scene = SceneORM(
        user_id=current_user.id,
        manuscript_id=payload.manuscript_id,
        chapter_id=chapter.id,
        position=next_pos,
        setting=payload.setting,
        emotional_tone=payload.emotional_tone,
        characters=payload.characters,
        scene_description=payload.scene_description,
        scene_request=request_dict,
        status="processing",
    )
    db.add(scene)
    await db.commit()
    await db.refresh(scene)

    background_tasks.add_task(
        run_prose_scene_in_background,
        scene.id,
        request_dict,
        current_user.id,
        chapter.summary or "",
        prior_tail,
        chapter.book_id,
    )

    return {
        "scene_id": str(scene.id),
        "chapter_id": str(chapter.id),
        "position": next_pos,
        "status": scene.status,
    }


@router.post(
    "/chapters/{chapter_id}/scenes",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_scene(
    chapter_id: UUID,
    payload: ManualSceneCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a scene WITHOUT LLM generation — a blank/manual draft to write in.

    Unlike /scenes/generate this spends no model quota: it just appends an
    (optionally pre-filled) scene row the author can then edit via PUT
    /scenes/{id}/content. A scene created with content is immediately part of
    the chapter's prose (continuity/export read it), so status is 'completed'
    when content is supplied, else 'draft'."""
    chapter = await _owned_chapter(chapter_id, current_user, db)
    next_pos = (
        await db.execute(
            select(func.coalesce(func.max(SceneORM.position), -1)).where(
                SceneORM.chapter_id == chapter.id
            )
        )
    ).scalar_one() + 1
    content = payload.content or ""
    scene = SceneORM(
        user_id=current_user.id,
        chapter_id=chapter.id,
        position=next_pos,
        title=payload.title,
        setting=payload.setting,
        emotional_tone=payload.emotional_tone,
        characters=payload.characters,
        content=content,
        word_count=len(content.split()),
        status="completed" if content.strip() else "draft",
    )
    db.add(scene)
    await db.commit()
    await db.refresh(scene)
    return {
        "scene_id": str(scene.id),
        "chapter_id": str(chapter.id),
        "position": next_pos,
        "status": scene.status,
    }


# --- Draft editing -----------------------------------------------------------------


@router.put("/scenes/{scene_id}/content", response_model=dict)
async def update_scene_content(
    scene_id: UUID,
    payload: SceneContentUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Save an edit to the draft prose, snapshotting the previous state."""
    scene = (
        await db.execute(
            select(SceneORM).where(
                SceneORM.id == scene_id, SceneORM.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )

    previous = scene_text(scene)
    if previous:
        db.add(
            SceneRevisionORM(
                scene_id=scene.id,
                content=previous,
                word_count=len(previous.split()),
                source="edited" if scene.content else "generated",
            )
        )
    scene.content = payload.content
    scene.word_count = len(payload.content.split())
    scene.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "id": str(scene.id),
        "word_count": scene.word_count,
        "updated_at": scene.updated_at.isoformat(),
    }


@router.get("/scenes/{scene_id}/revisions", response_model=dict)
async def list_scene_revisions(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    scene = (
        await db.execute(
            select(SceneORM)
            .options(selectinload(SceneORM.revisions))
            .where(SceneORM.id == scene_id, SceneORM.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if not scene:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )
    return {
        "scene_id": str(scene.id),
        "revisions": [
            {
                "id": str(r.id),
                "word_count": r.word_count,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "content": r.content,
            }
            for r in scene.revisions
        ],
    }


@router.patch("/scenes/{scene_id}/position", response_model=dict)
async def move_scene(
    scene_id: UUID,
    payload: PositionUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder a scene within its chapter."""
    scene = (
        await db.execute(
            select(SceneORM).where(
                SceneORM.id == scene_id, SceneORM.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if not scene or scene.chapter_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scene not found in a chapter",
        )
    siblings = (
        (
            await db.execute(
                select(SceneORM)
                .where(SceneORM.chapter_id == scene.chapter_id)
                .order_by(SceneORM.position)
            )
        )
        .scalars()
        .all()
    )
    ordered = [s for s in siblings if s.id != scene.id]
    new_pos = min(payload.position, len(ordered))
    ordered.insert(new_pos, scene)
    for idx, s in enumerate(ordered):
        s.position = idx
    await db.commit()
    return {"id": str(scene.id), "position": scene.position}


# --- Export ---------------------------------------------------------------------


@router.get("/{book_id}/export")
async def export_book(
    book_id: UUID,
    format: str = "md",
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Export the book's current draft state as md | docx | epub."""
    if format not in CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown format '{format}' (md | docx | epub)",
        )
    book = await _owned_book(book_id, current_user, db)
    chapters = (
        (
            await db.execute(
                select(ChapterORM)
                .options(selectinload(ChapterORM.scenes))
                .where(ChapterORM.book_id == book.id)
                .order_by(ChapterORM.position)
            )
        )
        .scalars()
        .all()
    )

    export = BookExport(
        title=book.title,
        author=book.author or "",
        synopsis=book.synopsis or "",
        chapters=[
            ChapterExport(
                title=c.title,
                summary=c.summary or "",
                scenes=[
                    SceneExport(title=s.title or "", content=scene_text(s))
                    for s in sorted(c.scenes, key=lambda s: s.position)
                    if scene_text(s)
                ],
            )
            for c in chapters
        ],
    )

    filename_stem = (
        "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in book.title)
        .strip()
        .replace(" ", "-")
        .lower()
        or "book"
    )

    if format == "md":
        body: bytes = to_markdown(export).encode()
    elif format == "docx":
        body = to_docx(export)
    else:
        body = to_epub(export)

    return Response(
        content=body,
        media_type=CONTENT_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename_stem}.{format}"'
        },
    )
