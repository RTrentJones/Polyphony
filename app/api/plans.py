"""Planning endpoints: outlines/beat sheets, plot threads, continuity checks."""

from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.books import _owned_book, _owned_chapter
from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.logging_config import setup_logging
from app.core.orm_models import (
    Book as BookORM,
    BookPlan as BookPlanORM,
    Chapter as ChapterORM,
    Character as CharacterORM,
    ContinuityReport as ContinuityReportORM,
    PlotThread as PlotThreadORM,
    PlotThreadEvent as PlotThreadEventORM,
    Scene as SceneORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.exports.builder import scene_text
from app.jobs import repository as jobs_repo
from app.planning.outline import generate_outline, validate_outline_nodes

logger = setup_logging("api.plans")

router = APIRouter()


# --- Payloads -------------------------------------------------------------------


class PlanUpsert(BaseModel):
    kind: str = Field(default="outline", pattern="^(outline|beat_sheet)$")
    content: list = Field(default_factory=list)


class PlanGenerate(BaseModel):
    kind: str = Field(default="outline", pattern="^(outline|beat_sheet)$")
    chapters_target: int = Field(default=12, ge=3, le=40)


class PromoteNode(BaseModel):
    node_index: int = Field(..., ge=0)


class ThreadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = None


class ThreadUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(open|resolved|abandoned)$")
    color: Optional[str] = None


class ThreadEventCreate(BaseModel):
    note: str = Field(..., min_length=1)
    kind: str = Field(default="development", pattern="^(setup|development|payoff)$")
    scene_id: Optional[UUID] = None
    chapter_id: Optional[UUID] = None


class ContinuityRequest(BaseModel):
    chapter_id: Optional[UUID] = None  # None => whole book


# --- Plans (outline / beat sheet) --------------------------------------------------


def _plan_dict(p: BookPlanORM) -> dict:
    return {
        "id": str(p.id),
        "book_id": str(p.book_id),
        "kind": p.kind,
        "content": p.content or [],
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/books/{book_id}/plans", response_model=dict)
async def list_plans(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    plans = (
        (await db.execute(select(BookPlanORM).where(BookPlanORM.book_id == book.id)))
        .scalars()
        .all()
    )
    return {"plans": [_plan_dict(p) for p in plans]}


@router.put("/books/{book_id}/plans", response_model=dict)
async def upsert_plan(
    book_id: UUID,
    payload: PlanUpsert,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the book's plan of the given kind."""
    book = await _owned_book(book_id, current_user, db)
    try:
        content = validate_outline_nodes(payload.content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    plan = (
        await db.execute(
            select(BookPlanORM).where(
                BookPlanORM.book_id == book.id, BookPlanORM.kind == payload.kind
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        plan = BookPlanORM(book_id=book.id, kind=payload.kind, content=content)
        db.add(plan)
    else:
        plan.content = content
    await db.commit()
    await db.refresh(plan)
    return _plan_dict(plan)


@router.post("/books/{book_id}/plans/generate", response_model=dict)
async def generate_plan(
    book_id: UUID,
    payload: PlanGenerate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM-draft an outline/beat sheet from the synopsis + character bible."""
    book = await _owned_book(book_id, current_user, db)
    if not book.synopsis:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add a synopsis to the book before generating an outline",
        )
    await check_user_budget(db, current_user.id)

    characters = (
        (await db.execute(select(CharacterORM).where(CharacterORM.book_id == book.id)))
        .scalars()
        .all()
    )
    bible = "\n".join(
        f"- {c.name}: {c.role or ''} {c.description or ''}".strip() for c in characters
    )

    try:
        nodes = await generate_outline(
            title=book.title,
            synopsis=book.synopsis,
            genre=book.genre or "",
            character_bible=bible,
            kind=payload.kind,
            chapters_target=payload.chapters_target,
            user_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    plan = (
        await db.execute(
            select(BookPlanORM).where(
                BookPlanORM.book_id == book.id, BookPlanORM.kind == payload.kind
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        plan = BookPlanORM(book_id=book.id, kind=payload.kind, content=nodes)
        db.add(plan)
    else:
        plan.content = nodes
    await db.commit()
    await db.refresh(plan)
    return _plan_dict(plan)


@router.post("/books/{book_id}/plans/promote", response_model=dict)
async def promote_plan_node(
    book_id: UUID,
    payload: PromoteNode,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote a top-level outline node into a real Chapter."""
    book = await _owned_book(book_id, current_user, db)
    plan = (
        await db.execute(
            select(BookPlanORM).where(
                BookPlanORM.book_id == book.id, BookPlanORM.kind == "outline"
            )
        )
    ).scalar_one_or_none()
    if plan is None or not plan.content:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No outline to promote from"
        )
    nodes = plan.content
    if payload.node_index >= len(nodes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="node_index out of range"
        )
    node = nodes[payload.node_index]

    next_pos = (
        await db.execute(
            select(func.coalesce(func.max(ChapterORM.position), -1)).where(
                ChapterORM.book_id == book.id
            )
        )
    ).scalar_one() + 1

    child_summaries = "\n".join(
        f"- {child.get('title', '')}: {child.get('summary', '')}"
        for child in node.get("children", [])
    )
    summary = node.get("summary", "")
    if child_summaries:
        summary = f"{summary}\n\nPlanned beats:\n{child_summaries}".strip()

    chapter = ChapterORM(
        book_id=book.id,
        title=node.get("title", f"Chapter {next_pos + 1}"),
        summary=summary,
        position=next_pos,
    )
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    return {
        "chapter_id": str(chapter.id),
        "title": chapter.title,
        "position": chapter.position,
    }


# --- Plot threads ------------------------------------------------------------------


def _thread_dict(t: PlotThreadORM, events: Optional[list] = None) -> dict:
    data = {
        "id": str(t.id),
        "book_id": str(t.book_id),
        "name": t.name,
        "description": t.description,
        "status": t.status,
        "color": t.color,
    }
    if events is not None:
        data["events"] = [
            {
                "id": str(e.id),
                "note": e.note,
                "kind": e.kind,
                "scene_id": str(e.scene_id) if e.scene_id else None,
                "chapter_id": str(e.chapter_id) if e.chapter_id else None,
                "position": e.position,
            }
            for e in events
        ]
    return data


@router.get("/books/{book_id}/threads", response_model=dict)
async def list_threads(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    threads = (
        (
            await db.execute(
                select(PlotThreadORM).where(PlotThreadORM.book_id == book.id)
            )
        )
        .scalars()
        .all()
    )
    result = []
    for t in threads:
        events = (
            (
                await db.execute(
                    select(PlotThreadEventORM)
                    .where(PlotThreadEventORM.thread_id == t.id)
                    .order_by(PlotThreadEventORM.position)
                )
            )
            .scalars()
            .all()
        )
        result.append(_thread_dict(t, events))
    return {"threads": result}


@router.post(
    "/books/{book_id}/threads",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    book_id: UUID,
    payload: ThreadCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    thread = PlotThreadORM(
        book_id=book.id,
        name=payload.name,
        description=payload.description,
        color=payload.color,
    )
    db.add(thread)
    await db.commit()
    await db.refresh(thread)
    return _thread_dict(thread)


async def _owned_thread(
    thread_id: UUID, user: UserORM, db: AsyncSession
) -> PlotThreadORM:
    thread = (
        await db.execute(
            select(PlotThreadORM)
            .join(BookORM, PlotThreadORM.book_id == BookORM.id)
            .where(PlotThreadORM.id == thread_id, BookORM.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    return thread


@router.patch("/threads/{thread_id}", response_model=dict)
async def update_thread(
    thread_id: UUID,
    payload: ThreadUpdate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await _owned_thread(thread_id, current_user, db)
    for field_name in ("name", "description", "status", "color"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(thread, field_name, value)
    await db.commit()
    return _thread_dict(thread)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await _owned_thread(thread_id, current_user, db)
    await db.delete(thread)
    await db.commit()
    return None


@router.post(
    "/threads/{thread_id}/events",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def add_thread_event(
    thread_id: UUID,
    payload: ThreadEventCreate,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    thread = await _owned_thread(thread_id, current_user, db)
    if payload.chapter_id:
        await _owned_chapter(payload.chapter_id, current_user, db)
    next_pos = (
        await db.execute(
            select(func.coalesce(func.max(PlotThreadEventORM.position), -1)).where(
                PlotThreadEventORM.thread_id == thread.id
            )
        )
    ).scalar_one() + 1
    event = PlotThreadEventORM(
        thread_id=thread.id,
        note=payload.note,
        kind=payload.kind,
        scene_id=payload.scene_id,
        chapter_id=payload.chapter_id,
        position=next_pos,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return {"id": str(event.id), "position": event.position}


# --- Continuity ---------------------------------------------------------------------


async def _build_fact_sheet(book: BookORM, db: AsyncSession) -> str:
    """Bible + open threads + chapter summaries — what the prose must not contradict."""
    characters = (
        (await db.execute(select(CharacterORM).where(CharacterORM.book_id == book.id)))
        .scalars()
        .all()
    )
    threads = (
        (
            await db.execute(
                select(PlotThreadORM).where(
                    PlotThreadORM.book_id == book.id,
                    PlotThreadORM.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
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
    parts = [f"Book: {book.title}. {book.synopsis or ''}"]
    if characters:
        parts.append("Characters:")
        parts.extend(
            f"- {c.name}: {c.role or ''}; {c.description or ''}; goals: {c.goals or ''}"
            for c in characters
        )
    if threads:
        parts.append("Open plot threads:")
        parts.extend(f"- {t.name}: {t.description or ''}" for t in threads)
    if chapters:
        parts.append("Chapter summaries so far:")
        parts.extend(
            f"- Ch{c.position + 1} {c.title}: {c.summary or ''}" for c in chapters
        )
    return "\n".join(parts)


async def _collect_prose(
    book_id: UUID, chapter_id: UUID | None, db: AsyncSession
) -> str:
    """Ordered prose of a chapter (or the whole book) — the continuity input.

    Derived data: the continuity job handler recomputes it from the DB at run
    time instead of snapshotting up to a whole book of prose into the payload.
    """
    scene_query = (
        select(SceneORM)
        .join(ChapterORM, SceneORM.chapter_id == ChapterORM.id)
        .where(ChapterORM.book_id == book_id)
        .order_by(ChapterORM.position, SceneORM.position)
    )
    if chapter_id:
        scene_query = scene_query.where(SceneORM.chapter_id == chapter_id)
    scenes = (await db.execute(scene_query)).scalars().all()
    return "\n\n".join(scene_text(s) for s in scenes if scene_text(s))


@router.post("/books/{book_id}/continuity", response_model=dict)
async def start_continuity_check(
    book_id: UUID,
    payload: ContinuityRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Run a continuity check on a chapter (default) or the whole book."""
    book = await _owned_book(book_id, current_user, db)
    await check_user_budget(db, current_user.id)

    if payload.chapter_id:
        await _owned_chapter(payload.chapter_id, current_user, db)

    prose = await _collect_prose(book.id, payload.chapter_id, db)
    if not prose:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No prose to check yet",
        )

    from app.llm.providers import active_provider, resolve_model

    report = ContinuityReportORM(
        book_id=book.id,
        chapter_id=payload.chapter_id,
        scope="chapter" if payload.chapter_id else "book",
        status="processing",
        model=resolve_model(active_provider(), fast=False),
    )
    db.add(report)
    await db.flush()
    # Job + report row commit atomically; the handler recomputes prose and
    # fact sheet from the DB (derived data) at run time.
    await jobs_repo.enqueue(
        db,
        kind="continuity_check",
        payload={
            "report_id": str(report.id),
            "user_id": str(current_user.id),
        },
        user_id=current_user.id,
        max_attempts=2,
    )
    await db.commit()
    await db.refresh(report)

    return {"report_id": str(report.id), "status": report.status}


@router.get("/books/{book_id}/continuity", response_model=dict)
async def list_continuity_reports(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    book = await _owned_book(book_id, current_user, db)
    reports = (
        (
            await db.execute(
                select(ContinuityReportORM)
                .where(ContinuityReportORM.book_id == book.id)
                .order_by(ContinuityReportORM.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {
        "reports": [
            {
                "id": str(r.id),
                "scope": r.scope,
                "chapter_id": str(r.chapter_id) if r.chapter_id else None,
                "status": r.status,
                "findings": r.findings,
                "model": r.model,
                "tokens_used": r.tokens_used,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]
    }
