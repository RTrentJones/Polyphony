"""Generate every planned scene of a chapter, one after another (SPEC-10 follow-up).

A promoted chapter carries its outline node's children as DATA in
`chapters.planned_beats` (SPEC-23; app/api/plans.py `_promote_node`), each with
its own cast, setting and tone; the generate-scene modal offers each as a
one-click description. "Generate all planned scenes" writes them all. A scene
written from a beat carries the beat's id (`scene_request.planned_beat_id`;
migration 0019 linked the scenes written before SPEC-23), and that id is the
only thing that says a beat is written.

The scenes must run IN ORDER, each seeing the one before: a scene job reads its
prior scene's tail from the DB when it starts (`prose.scene_prior_tail`). The
worker is single-consumer, but enqueueing every job up front would still not
guarantee that — a quota pause re-queues job 1 for later while job 2 is already
claimable, and a failed job 1 would leave job 2 writing on a gap. So the batch
is a CHAIN: the endpoint creates the first scene and its job; each job, once
its scene is completed, creates the next scene and enqueues its job. A failed or
cancelled scene ends the chain (nothing further is spent), and the author can
click again: beats that already have a live scene are skipped.

Each job in the chain holds a dedupe key `<batch key>:<index>` (SPEC-07), so a
repeat request while any scene of the batch is in flight returns that batch.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import log_business_event, setup_logging
from app.core.orm_models import Chapter, Job, Scene
from app.jobs import repository as jobs_repo

logger = setup_logging("jobs.scene_batch")

# Scenes in these states do not count as "this beat is already written".
_RETRYABLE_STATUSES = ("failed", "cancelled")


def batch_key(user_id: UUID, chapter_id: UUID) -> str:
    """One batch in flight per chapter: the dedupe-key prefix of its jobs."""
    return f"scene-batch:{user_id}:{chapter_id}"


async def find_active_batch_job(session: AsyncSession, key: str) -> Job | None:
    """The in-flight job of the batch holding `key`, if any."""
    return (
        await session.execute(
            select(Job)
            .where(
                Job.dedupe_key.startswith(f"{key}:", autoescape=True),
                Job.status.in_(jobs_repo.ACTIVE_STATUSES),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def written_beats(session: AsyncSession, chapter_id: UUID) -> dict[str, str]:
    """{planned_beat_id: scene id} over the chapter's live (not failed/cancelled)
    scenes: the beats already written (planned_beat.unwritten_beats). When two
    scenes share a beat, the first in chapter order wins (S7)."""
    rows = await session.execute(
        select(Scene.id, Scene.scene_request)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.deleted_at.is_(None),
            Scene.status.notin_(_RETRYABLE_STATUSES),
        )
        .order_by(Scene.position)
    )
    out: dict[str, str] = {}
    for scene_id, r in rows:
        if isinstance(r, dict) and r.get("planned_beat_id"):
            out.setdefault(str(r["planned_beat_id"]), str(scene_id))
    return out


async def add_scene_job(
    session: AsyncSession,
    *,
    chapter_id: UUID,
    book_id: UUID,
    user_id: UUID,
    request: dict,
    job_kind: str,
    dedupe_key: Optional[str],
    source_id: Optional[UUID] = None,
    batch: Optional[dict] = None,
) -> tuple[Scene, Job]:
    """Append a 'processing' scene to the chapter and enqueue its generation job.

    Flushed, NOT committed: scene and job commit atomically in the caller's
    transaction, and the caller handles a position clash (IntegrityError) by
    rolling back and calling again. Compare `job.domain_id` with `scene.id`: a
    different id means an identical request (same dedupe key) won the race.
    """
    next_pos = (
        await session.execute(
            select(func.coalesce(func.max(Scene.position), -1)).where(
                Scene.chapter_id == chapter_id, Scene.deleted_at.is_(None)
            )
        )
    ).scalar_one() + 1
    scene = Scene(
        user_id=user_id,
        book_id=book_id,
        source_id=source_id,
        chapter_id=chapter_id,
        position=next_pos,
        setting=request.get("setting"),
        emotional_tone=request.get("emotional_tone"),
        characters=request.get("characters"),
        character_ids=request.get("character_ids"),
        scene_description=request.get("scene_description"),
        scene_request=request,
        status="processing",
    )
    session.add(scene)
    await session.flush()
    payload = {
        "scene_id": str(scene.id),
        "request": request,
        "user_id": str(user_id),
        "book_id": str(book_id),
    }
    if batch is not None:
        payload["batch"] = batch
    job = await jobs_repo.enqueue(
        session,
        kind=job_kind,
        payload=payload,
        user_id=user_id,
        max_attempts=1,  # a retry re-spends LLM budget; the author can re-trigger
        dedupe_key=dedupe_key,
        domain=("scene", scene.id),
    )
    return scene, job


async def enqueue_next_planned_scene(
    payload: dict, job_id: Optional[UUID] = None
) -> Optional[str]:
    """After a batch scene completed: create the next scene and enqueue its job.

    Returns the new scene id, or None when the batch is done or cannot go on
    (its chapter was trashed meanwhile, or the author cancelled this job).
    Called by the scene handlers only after their workflow reported success, so
    a failed or cancelled scene ends the chain without spending anything further.

    `job_id` is the job that just wrote its scene. Its row is read FOR UPDATE
    in the same transaction that inserts the next job, so a cancel cannot
    commit in between: either the cancel lands first and the chain stops here,
    or the next job commits first and the cancel endpoint cancels it as the
    batch's queued successor (`cancel_successors`).
    """
    from app.core.database import get_async_session

    batch = payload.get("batch") or {}
    remaining = list(batch.get("remaining") or [])
    if not remaining:
        return None
    request, rest = remaining[0], remaining[1:]
    index = int(batch.get("index") or 0) + 1
    user_id = UUID(payload["user_id"])
    book_id = UUID(payload["book_id"])
    chapter_id = UUID(batch["chapter_id"])
    next_batch = {**batch, "index": index, "remaining": rest}
    dedupe_key = f"{batch['key']}:{index}" if batch.get("key") else None

    for attempt in (1, 2):
        async with get_async_session() as session:
            if job_id is not None:
                current = (
                    await session.execute(
                        select(Job).where(Job.id == job_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if current is not None and current.cancel_requested:
                    log_business_event(
                        logger,
                        "scene_batch_stopped",
                        chapter_id=str(chapter_id),
                        reason="cancelled",
                        left=len(remaining),
                    )
                    return None
            chapter = await session.get(Chapter, chapter_id)
            if chapter is None or chapter.deleted_at is not None:
                log_business_event(
                    logger,
                    "scene_batch_stopped",
                    chapter_id=str(chapter_id),
                    reason="chapter gone",
                    left=len(remaining),
                )
                return None
            try:
                scene, job = await add_scene_job(
                    session,
                    chapter_id=chapter_id,
                    book_id=book_id,
                    user_id=user_id,
                    request=request,
                    job_kind=batch.get("kind") or "generate_prose_scene",
                    dedupe_key=dedupe_key,
                    batch=next_batch,
                )
                if job.domain_id != scene.id:  # already enqueued (a re-run)
                    await session.rollback()
                    return str(job.domain_id)
                scene_id = str(scene.id)
                await session.commit()
            except IntegrityError:
                # A concurrent append took the position; recompute once.
                await session.rollback()
                if attempt == 2:
                    raise
                continue
        log_business_event(
            logger,
            "scene_batch_next",
            chapter_id=str(chapter_id),
            scene_id=scene_id,
            index=index,
            total=batch.get("total"),
        )
        return scene_id
    return None


async def cancel_successors(session: AsyncSession, job: Job) -> list[Job]:
    """Cancel the queued jobs of the batch `job` belongs to (in this
    transaction) and return them, so the caller can run their on_cancel hooks
    after committing. Cancelling one scene of a batch stops the whole batch."""
    key = ((job.payload or {}).get("batch") or {}).get("key")
    if not key:
        return []
    queued = (
        (
            await session.execute(
                select(Job).where(
                    Job.dedupe_key.startswith(f"{key}:", autoescape=True),
                    Job.status == "queued",
                    Job.id != job.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for other in queued:
        await jobs_repo.mark_cancelled(session, other)
    return list(queued)
