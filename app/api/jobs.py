"""Job status + cancel (SPEC-07): one observable lifecycle for every LLM job.

`queued → running → paused ↔ queued → succeeded | failed | cancelled`. In the
table a paused job is `queued` with `paused_since` set (so claim, idle and the
dedupe index need no special case); this API reports it as `paused` with the
time it resumes. A `dead` job is reported as `failed`.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.orm_models import Job as JobORM, User as UserORM
from app.core.security import get_current_active_user
from app.jobs import repository as jobs_repo
from app.jobs.context import JobContext

router = APIRouter()


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    from datetime import timezone

    if dt.tzinfo is None:  # sqlite hands back naive datetimes
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def public_status(job: JobORM) -> str:
    if job.status == "queued" and job.paused_since is not None:
        return "paused"
    if job.status == "dead":
        return "failed"
    return job.status


def job_view(job: JobORM) -> dict:
    state = public_status(job)
    progress = None
    if isinstance(job.checkpoint, dict) and job.checkpoint.get("progress"):
        progress = job.checkpoint["progress"]
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": state,
        "available_at": _iso(job.available_at),
        # When a paused job is due to resume: the UI's "resumes ~14:05".
        "resume_at": _iso(job.available_at) if state == "paused" else None,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "pause_count": job.pause_count or 0,
        "cancel_requested": bool(job.cancel_requested),
        "cancellable": state in ("queued", "paused", "running")
        and not job.cancel_requested,
        "error": job.error,
        "progress": progress,  # {stage, done, total} from the checkpoint (SPEC-08)
        "domain": (
            {"type": job.domain_type, "id": str(job.domain_id)}
            if job.domain_type and job.domain_id
            else None
        ),
        "created_at": _iso(job.created_at),
        "finished_at": _iso(job.finished_at),
    }


async def latest_jobs(db: AsyncSession, domain_type: str, ids: list) -> dict[str, dict]:
    """The newest job for each domain row, as a compact summary for list
    payloads, so a page can render a row's badge (and resume time) without a
    request per row."""
    if not ids:
        return {}
    rows = (
        (
            await db.execute(
                select(JobORM)
                .where(JobORM.domain_type == domain_type, JobORM.domain_id.in_(ids))
                .order_by(JobORM.created_at.desc(), JobORM.id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, dict] = {}
    for job in rows:
        key = str(job.domain_id)
        if key in out:
            continue
        view = job_view(job)
        out[key] = {
            "job_id": view["id"],
            "status": view["status"],
            "resume_at": view["resume_at"],
            "progress": view["progress"],
        }
    return out


async def _owned_job(job_id: UUID, user: UserORM, db: AsyncSession) -> JobORM:
    job = (
        await db.execute(
            select(JobORM)
            .where(JobORM.id == job_id, JobORM.user_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        # Another user's job is indistinguishable from a missing one.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return job


@router.get("/jobs", response_model=dict)
async def list_active_jobs(
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The author's queued, running and paused jobs, oldest first — the Usage
    page's "what is waiting for quota" list (SPEC-12)."""
    rows = (
        (
            await db.execute(
                select(JobORM)
                .where(
                    JobORM.user_id == current_user.id,
                    JobORM.status.in_(("queued", "running")),
                )
                .order_by(JobORM.created_at, JobORM.id)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {"jobs": [job_view(j) for j in rows]}


@router.get("/jobs/{job_id}", response_model=dict)
async def get_job(
    job_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    job = (
        await db.execute(
            select(JobORM).where(JobORM.id == job_id, JobORM.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return job_view(job)


@router.post("/jobs/{job_id}/cancel", response_model=dict)
async def cancel_job(
    job_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a job. Queued or paused: cancelled now and its domain row moved
    (on_cancel). Running: flagged, and the workflow stops at its next seam
    between model calls — a call in flight cannot be recalled."""
    from app.jobs.handlers import HANDLERS
    from app.jobs.scene_batch import cancel_successors
    from app.jobs.worker import _hook

    job = await _owned_job(job_id, current_user, db)
    if job.status == "running":
        job.cancel_requested = True
        # The flush takes the row lock a batch's next-scene enqueue reads under
        # (scene_batch.enqueue_next_planned_scene), so a successor it already
        # committed is visible here and is cancelled with this job.
        await db.flush()
        successors = await cancel_successors(db, job)
        await db.commit()
        await db.refresh(job)
        for other in successors:
            await _hook(
                HANDLERS.get(other.kind),
                "on_cancel",
                other.payload or {},
                JobContext(job_id=other.id, kind=other.kind),
            )
        return job_view(job)
    if job.status != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {public_status(job)}",
        )
    await jobs_repo.mark_cancelled(db, job)
    await db.commit()
    await db.refresh(job)

    await _hook(
        HANDLERS.get(job.kind),
        "on_cancel",
        job.payload or {},
        JobContext(job_id=job.id, kind=job.kind),
    )
    return job_view(job)
