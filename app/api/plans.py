"""Planning endpoints: outlines/beat sheets, plot threads, continuity checks."""

from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.books import _owned_book, _owned_chapter
from app.core.budget import (
    check_user_budget,
)
from app.core.database import get_db
from app.core.logging_config import setup_logging
from app.core.orm_models import (
    Book as BookORM,
    BookPlan as BookPlanORM,
    CanonEntry as CanonEntryORM,
    Chapter as ChapterORM,
    Character as CharacterORM,
    ContinuityReport as ContinuityReportORM,
    PlotThread as PlotThreadORM,
    PlotThreadEvent as PlotThreadEventORM,
    Scene as SceneORM,
    StyleGuide as StyleGuideORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.exports.builder import scene_text
from app.jobs import repository as jobs_repo
from app.llm.tier import get_tier
from app.planning.canon import render_bible
from app.planning.outline import CastIndex, validate_outline_nodes
from app.versioning import entities as entity_versions
from app.versioning import repository as versions_repo

logger = setup_logging("api.plans")

router = APIRouter()


async def _snapshot_plan(db, book, plan, content, reason, user_id) -> None:
    """Append a version of a plan's new state (docs/ADR-002 §5). Never commits.

    Runs after the plan row is flushed (so plan.id exists) and before the
    caller's commit, so the version and the live row land atomically —
    regeneration/edit can never clobber a prior outline.
    """
    await db.flush()
    await versions_repo.snapshot(
        db,
        book_id=book.id,
        entity_type="book_plan",
        entity_id=plan.id,
        content={"kind": plan.kind, "content": content, "premise": plan.premise},
        reason=reason,
        created_by=user_id,
    )


# --- Payloads -------------------------------------------------------------------


class PlanUpsert(BaseModel):
    kind: str = Field(default="outline", pattern="^(outline|beat_sheet)$")
    content: list = Field(default_factory=list)


class PlanGenerate(BaseModel):
    kind: str = Field(default="outline", pattern="^(outline|beat_sheet)$")
    chapters_target: int = Field(default=12, ge=3, le=40)
    # Stop after the skeleton for the author to confirm the premise (SPEC-10).
    # None = the tier's default: on for free, where a misread premise wastes
    # the most of a scarce quota.
    confirm_premise: Optional[bool] = None


class PremiseConfirm(BaseModel):
    """The author's confirmation; an edited field replaces the model's and is
    authoritative for the chapters stage."""

    premise_restated: Optional[str] = Field(None, max_length=20_000)
    central_conflict: Optional[str] = Field(None, max_length=5_000)


class PromoteNode(BaseModel):
    """By `node_id` (stable) or `node_index` (legacy); one is required."""

    node_id: Optional[UUID] = None
    node_index: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _one_of(self):
        if self.node_id is None and self.node_index is None:
            raise ValueError("node_id or node_index is required")
        return self


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


async def _book_cast(db: AsyncSession, book_id: UUID) -> CastIndex:
    query = select(CharacterORM).where(CharacterORM.book_id == book_id)
    if hasattr(CharacterORM, "deleted_at"):
        query = query.where(CharacterORM.deleted_at.is_(None))
    return CastIndex((await db.execute(query)).scalars().all())


def _plan_dict(p: BookPlanORM) -> dict:
    return {
        "id": str(p.id),
        "book_id": str(p.book_id),
        "kind": p.kind,
        "content": p.content or [],
        "status": getattr(p, "status", "ready"),
        "stage": getattr(p, "stage", None),
        "warnings": getattr(p, "warnings", None) or [],
        "error": getattr(p, "error", None),
        "premise": getattr(p, "premise", None),
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
    from app.api.jobs import latest_jobs

    jobs = await latest_jobs(db, "book_plan", [p.id for p in plans])
    return {"plans": [{**_plan_dict(p), "job": jobs.get(str(p.id))} for p in plans]}


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
        content = validate_outline_nodes(
            payload.content, cast=await _book_cast(db, book.id), strict=True
        )
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
        reason = "created"
    else:
        plan.content = content
        reason = "edited"
    await _snapshot_plan(db, book, plan, content, reason, current_user.id)
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
    """Draft an outline/beat sheet from the canon, as a background job.

    kind='outline' on a staged-capable tier with budget to spare runs the STAGED,
    canon-grounded, fidelity-gated generator (~6 calls). Otherwise (beat_sheet,
    staged disabled, or budget too low to finish) it degrades to a single call
    (docs/BRD.md R5, R7.3) — also a job. Returns {plan_id, job_id, status}; poll
    GET /jobs/{job_id} or GET /books/{id}/plans.
    """
    book = await _owned_book(book_id, current_user, db)
    if not book.synopsis:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add a synopsis to the book before generating an outline",
        )
    await check_user_budget(db, current_user.id)

    characters = (
        (
            await db.execute(
                select(CharacterORM).where(
                    CharacterORM.book_id == book.id, CharacterORM.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    canon_entries = (
        (
            await db.execute(
                select(CanonEntryORM)
                .where(
                    CanonEntryORM.book_id == book.id, CanonEntryORM.deleted_at.is_(None)
                )
                .order_by(CanonEntryORM.position)
            )
        )
        .scalars()
        .all()
    )
    style = (
        await db.execute(select(StyleGuideORM).where(StyleGuideORM.book_id == book.id))
    ).scalar_one_or_none()
    # The FULL canon reaches the model — every cast field (render_characters),
    # plus canon entries and style. Was a one-line `name: role description` slice
    # that dropped goals/arc/relationships and the entire world (docs/BRD.md §1).
    bible = render_bible(characters, canon_entries, style)

    # Preflight (SPEC-12): the staged path is ~6 calls over the whole canon.
    # Take it only if the tier allows it AND the budget can plausibly finish
    # it; otherwise degrade to the single call rather than half-write a doomed
    # job, and refuse outright when even that does not fit. The estimator is
    # the one EstimateHint shows, so the refusal matches the number displayed.
    from app.core import estimates

    tier = get_tier()
    canon_chars = len(book.synopsis or "") + len(bible)
    staged = False
    if payload.kind == "outline" and tier.allow_staged_outline:
        assessed = await estimates.assess(
            db, current_user.id, estimates.outline(canon_chars=canon_chars)
        )
        staged = assessed["allowed"]
    if not staged:
        assessed = await estimates.assess(
            db,
            current_user.id,
            estimates.outline(canon_chars=canon_chars, staged=False),
        )
        if not assessed["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": assessed["reason"], "estimate": assessed},
            )

    plan = (
        await db.execute(
            select(BookPlanORM).where(
                BookPlanORM.book_id == book.id, BookPlanORM.kind == payload.kind
            )
        )
    ).scalar_one_or_none()
    # One in-flight generation per plan (SPEC-07): a second click returns the
    # job already running instead of re-spending the quota.
    dedupe_key = f"outline:{book.id}:{payload.kind}"
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None:
        return {
            "plan_id": str(existing.domain_id),
            "job_id": str(existing.id),
            "status": plan.status if plan is not None else "generating",
        }
    if plan is None:
        plan = BookPlanORM(book_id=book.id, kind=payload.kind, content=[])
        db.add(plan)
    # Cancel restores this when the previous content is intact (SPEC-07).
    prior_status = plan.status if plan.content else None
    if prior_status == "awaiting_confirmation":
        prior_status = "ready"
    confirm_premise = (
        payload.confirm_premise
        if payload.confirm_premise is not None
        else tier.name == "free"
    )
    plan.status = "generating"
    plan.stage = "skeleton" if staged else "single"
    plan.error = None
    plan.premise = None  # a new run restates the premise afresh
    await db.flush()
    # Both paths are jobs: the single call used to run inside this request,
    # where 429 backoff could outlive the proxy timeout (F13).
    job = await jobs_repo.enqueue(
        db,
        kind="generate_outline",
        payload={
            "plan_id": str(plan.id),
            "book_id": str(book.id),
            "user_id": str(current_user.id),
            "chapters_target": payload.chapters_target,
            "kind": payload.kind,
            "mode": "staged" if staged else "single",
            "prior_status": prior_status,
            "confirm_premise": bool(staged and confirm_premise),
        },
        user_id=current_user.id,
        max_attempts=1,  # a retry re-spends ~6 calls; the user can re-trigger
        dedupe_key=dedupe_key,
        domain=("book_plan", plan.id),
    )
    if job.domain_id != plan.id:  # lost a race to an identical request
        # Plain values first: the rollback expires every ORM instance.
        won = {"plan_id": str(job.domain_id), "job_id": str(job.id)}
        await db.rollback()
        return won
    await db.commit()
    return {"plan_id": str(plan.id), "job_id": str(job.id), "status": "generating"}


@router.post("/books/{book_id}/plans/{plan_id}/confirm-premise", response_model=dict)
async def confirm_premise(
    book_id: UUID,
    plan_id: UUID,
    payload: PremiseConfirm,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm (optionally edit) the restated premise and build the outline on it.

    The skeleton job ended with the plan `awaiting_confirmation` (SPEC-10). This
    enqueues the continuation: S2-S4 with the stored skeleton as authoritative,
    so S1 is never paid for twice. Returns {plan_id, job_id, status}."""
    book = await _owned_book(book_id, current_user, db)
    plan = (
        await db.execute(
            select(BookPlanORM).where(
                BookPlanORM.id == plan_id, BookPlanORM.book_id == book.id
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
    dedupe_key = f"outline:{book.id}:{plan.kind}"
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None:  # a double click: the continuation is running
        return {
            "plan_id": str(existing.domain_id),
            "job_id": str(existing.id),
            "status": plan.status,
        }
    if plan.status != "awaiting_confirmation" or not plan.premise:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan is not waiting for a premise confirmation",
        )
    await check_user_budget(db, current_user.id)

    premise = dict(plan.premise)
    edited = False
    for field_name in ("premise_restated", "central_conflict"):
        value = getattr(payload, field_name)
        if (
            value is not None
            and value.strip()
            and value.strip() != premise.get(field_name)
        ):
            premise[field_name] = value.strip()
            edited = True
    premise["confirmed"] = True
    premise["edited"] = edited
    plan.premise = premise  # a new dict, so the JSON column sees the change
    plan.status = "generating"
    plan.stage = "chapters"
    plan.error = None
    await db.flush()
    job = await jobs_repo.enqueue(
        db,
        kind="generate_outline",
        payload={
            "plan_id": str(plan.id),
            "book_id": str(book.id),
            "user_id": str(current_user.id),
            "chapters_target": int(premise.get("chapters_target") or 12),
            "kind": plan.kind,
            "mode": "staged",
            "premise_confirmed": True,
            # Cancelling the continuation goes back to waiting (the premise is
            # intact), not to "cancelled".
            "prior_status": "awaiting_confirmation",
        },
        user_id=current_user.id,
        max_attempts=1,
        dedupe_key=dedupe_key,
        domain=("book_plan", plan.id),
    )
    if job.domain_id != plan.id:  # lost a race to an identical request
        # Plain values first: the rollback expires every ORM instance.
        won = {"plan_id": str(job.domain_id), "job_id": str(job.id)}
        await db.rollback()
        return won
    await db.commit()
    return {"plan_id": str(plan.id), "job_id": str(job.id), "status": "generating"}


def _promoted_dict(chapter: ChapterORM, created: bool) -> dict:
    return {
        "chapter_id": str(chapter.id),
        "title": chapter.title,
        "position": chapter.position,
        "plan_node_id": str(chapter.plan_node_id) if chapter.plan_node_id else None,
        "created": created,
    }


async def _chapter_for_node(
    db: AsyncSession, book_id: UUID, node_id: UUID
) -> Optional[ChapterORM]:
    query = select(ChapterORM).where(
        ChapterORM.book_id == book_id, ChapterORM.plan_node_id == node_id
    )
    query = query.where(ChapterORM.deleted_at.is_(None))
    return (await db.execute(query.limit(1))).scalar_one_or_none()


async def _promote_node(
    db: AsyncSession, book_pk: UUID, node: dict, cast: CastIndex, user_id: UUID
) -> tuple[ChapterORM, bool]:
    """One outline node -> one Chapter, idempotent on the node's id (SPEC-10).

    Returns (chapter, created). The chapter carries the node's structural cast
    (resolved against the book's CURRENT cast: a character removed since the
    outline was drafted is dropped, not dangled) so a new scene defaults to it.
    """
    node_id = None
    try:
        node_id = UUID(str(node.get("node_id"))) if node.get("node_id") else None
    except ValueError:
        node_id = None
    if node_id is not None:
        existing = await _chapter_for_node(db, book_pk, node_id)
        if existing is not None:
            return existing, False

    character_ids: list[str] = []
    for ref in node.get("character_ids") or []:
        cid = cast.resolve(ref)
        if cid is not None and cid not in character_ids:
            character_ids.append(cid)
    pov_id = cast.resolve(node.get("pov")) if node.get("pov") else None
    if pov_id is not None and pov_id not in character_ids:
        character_ids.insert(0, pov_id)

    child_summaries = "\n".join(
        f"- {child.get('title', '')}: {child.get('summary', '')}"
        for child in node.get("children", [])
    )
    summary = node.get("summary", "")
    if child_summaries:
        summary = f"{summary}\n\nPlanned beats:\n{child_summaries}".strip()

    # Same as create_chapter's append: a concurrent append can take max+1 first;
    # the UNIQUE (book_id, position) constraint turns that into an
    # IntegrityError — recompute and retry once.
    for attempt in (1, 2):
        next_pos = (
            await db.execute(
                select(func.coalesce(func.max(ChapterORM.position), -1)).where(
                    ChapterORM.book_id == book_pk, ChapterORM.deleted_at.is_(None)
                )
            )
        ).scalar_one() + 1
        chapter = ChapterORM(
            book_id=book_pk,
            title=node.get("title", f"Chapter {next_pos + 1}"),
            summary=summary,
            position=next_pos,
            plan_node_id=node_id,
            pov_character_id=UUID(pov_id) if pov_id else None,
            character_ids=character_ids,
        )
        db.add(chapter)
        try:
            # A promoted chapter is versioned from v1 (SPEC-13).
            await entity_versions.record(
                db, "chapter", chapter, reason="created", created_by=user_id
            )
            await db.commit()
            break
        except IntegrityError:
            await db.rollback()
            # A concurrent promote of the same node won the unique index:
            # that chapter is this request's answer too.
            if node_id is not None:
                existing = await _chapter_for_node(db, book_pk, node_id)
                if existing is not None:
                    return existing, False
            if attempt == 2:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Chapter position conflict; please retry",
                )
    await db.refresh(chapter)
    return chapter, True


async def _outline_nodes(db: AsyncSession, book: BookORM) -> list:
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
    return list(plan.content)


@router.post("/books/{book_id}/plans/promote", response_model=dict)
async def promote_plan_node(
    book_id: UUID,
    payload: PromoteNode,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote a top-level outline node into a real Chapter.

    By `node_id` (stable across edits) or `node_index` (legacy). Promoting a
    node that already has a chapter returns that chapter (`created: false`)
    instead of duplicating it."""
    book = await _owned_book(book_id, current_user, db)
    nodes = await _outline_nodes(db, book)
    if payload.node_id is not None:
        node = next(
            (n for n in nodes if str(n.get("node_id")) == str(payload.node_id)), None
        )
        if node is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No outline node with that id",
            )
    else:
        if payload.node_index >= len(nodes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="node_index out of range",
            )
        node = nodes[payload.node_index]
    # Read everything off the ORM objects now: a rollback expires them.
    book_pk, user_id = book.id, current_user.id
    cast = await _book_cast(db, book_pk)
    chapter, created = await _promote_node(db, book_pk, node, cast, user_id)
    return _promoted_dict(chapter, created)


@router.post("/books/{book_id}/plans/promote-all", response_model=dict)
async def promote_all_plan_nodes(
    book_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote every top-level outline node, in order; nodes that already have
    a chapter are returned as they are. Safe to click twice."""
    book = await _owned_book(book_id, current_user, db)
    nodes = await _outline_nodes(db, book)
    book_pk, user_id = book.id, current_user.id
    cast = await _book_cast(db, book_pk)
    results = []
    for node in nodes:
        if isinstance(node, dict):
            chapter, created = await _promote_node(db, book_pk, node, cast, user_id)
            results.append(_promoted_dict(chapter, created))
    return {
        "chapters": results,
        "created": sum(1 for r in results if r["created"]),
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


async def _chapter_in_book(chapter_id: UUID, book_id: UUID, db: AsyncSession) -> bool:
    return (
        await db.execute(
            select(ChapterORM.id).where(
                ChapterORM.id == chapter_id,
                ChapterORM.book_id == book_id,
                ChapterORM.deleted_at.is_(None),
            )
        )
    ).first() is not None


async def _scene_in_book(scene_id: UUID, book_id: UUID, db: AsyncSession) -> bool:
    """A scene belongs to the book it carries (scenes are book-rooted, SPEC-04)."""
    return (
        await db.execute(
            select(SceneORM.id).where(
                SceneORM.id == scene_id,
                SceneORM.book_id == book_id,
                SceneORM.deleted_at.is_(None),
            )
        )
    ).first() is not None


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
    # An event may only point at the thread's own book: a chapter or scene the
    # user owns in another book (or anyone else's) is 404, not a dangling link.
    if payload.chapter_id and not await _chapter_in_book(
        payload.chapter_id, thread.book_id, db
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found"
        )
    if payload.scene_id and not await _scene_in_book(
        payload.scene_id, thread.book_id, db
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found"
        )
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
        .where(ChapterORM.deleted_at.is_(None), SceneORM.deleted_at.is_(None))
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
    # Preflight (SPEC-02, BRD R7.3): every chunk carries the whole fact sheet, so
    # a whole-book check on a big canon is expensive. Refuse with the estimate
    # rather than half-check the book.
    from app.core import estimates
    from app.planning.continuity import build_fact_sheet

    fact_sheet = await build_fact_sheet(db, book.id)
    est = estimates.continuity(
        fact_sheet_chars=len(fact_sheet), prose_words=len(prose.split())
    )
    assessed = await estimates.assess(db, current_user.id, est)
    if not assessed["allowed"]:
        message = assessed["reason"]
        if not payload.chapter_id:
            message += " Try checking one chapter instead."
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": message,
                "estimate": assessed,
                "estimate_tokens": est.tokens,
                "remaining_tokens": assessed["remaining_tokens_24h"],
            },
        )

    from app.llm.providers import active_provider, resolve_model

    dedupe_key = f"continuity:{book.id}:{payload.chapter_id or 'book'}"
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None:
        return {
            "report_id": str(existing.domain_id),
            "job_id": str(existing.id),
            "status": "processing",
        }

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
    job = await jobs_repo.enqueue(
        db,
        kind="continuity_check",
        payload={
            "report_id": str(report.id),
            "user_id": str(current_user.id),
        },
        user_id=current_user.id,
        max_attempts=2,
        dedupe_key=dedupe_key,
        domain=("continuity_report", report.id),
    )
    if job.domain_id != report.id:  # lost a race to an identical request
        # Plain values first: the rollback expires every ORM instance.
        won = {"report_id": str(job.domain_id), "job_id": str(job.id)}
        await db.rollback()
        return won
    await db.commit()
    await db.refresh(report)

    return {"report_id": str(report.id), "job_id": str(job.id), "status": report.status}


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
    from app.api.jobs import latest_jobs

    jobs = await latest_jobs(db, "continuity_report", [r.id for r in reports])
    return {
        "reports": [
            {
                "job": jobs.get(str(r.id)),
                "id": str(r.id),
                "scope": r.scope,
                "chapter_id": str(r.chapter_id) if r.chapter_id else None,
                "status": r.status,
                "findings": r.findings,
                "coverage": getattr(r, "coverage", None),
                "model": r.model,
                "tokens_used": r.tokens_used,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]
    }
