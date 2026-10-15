"""AI revision of a scene (SPEC-19, backlog B3): request, list, accept, discard.

`POST /books/scenes/{id}/revise` enqueues a `revise_scene` job (app/jobs/revise.py)
for a span of the draft — a character range, a beat, or the whole scene — after
the same preflight every LLM endpoint makes (budget, estimate, one in flight per
scene). The job stores its result as a proposal, `SceneRevision(source=
"ai_revise")`; the draft is untouched until the author accepts it.

Accepting snapshots the current draft first (the same rule as saving an edit),
so nothing the author wrote is lost (R4.x), and refuses if the draft changed
since the proposal was written. Discarding changes nothing but the proposal's
status.

Mounted beside the books router (prefix /api/v1/books) rather than inside it.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.books import owned_scene
from app.core.budget import check_user_budget
from app.core.database import get_db
from app.core.orm_models import (
    Scene as SceneORM,
    SceneBeat as SceneBeatORM,
    SceneRevision as SceneRevisionORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.jobs import repository as jobs_repo
from app.jobs import revise as revise_job
from app.orchestration import revise as rv

router = APIRouter()

# Scene statuses that mean "still being generated": its draft is not settled.
_GENERATING = ("processing", "paused")


class CharRange(BaseModel):
    """Character offsets into the draft as GET /proposals returns it (`draft`),
    end exclusive. Python string indices, i.e. Unicode code points — not
    UTF-16 code units, which is what a JS string index counts."""

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("range.end must be greater than range.start")
        return self


class ReviseRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)
    range: Optional[CharRange] = None
    beat_index: Optional[int] = Field(None, ge=0)
    # Optional guards from the client's view of the draft: a stale view (or an
    # offset counted in the wrong unit) is refused instead of revising the
    # wrong words.
    base_text_hash: Optional[str] = Field(None, max_length=64)
    selected_text: Optional[str] = None

    @model_validator(mode="after")
    def _one_span(self):
        if not self.instruction.strip():
            raise ValueError("instruction must not be blank")
        if self.range is not None and self.beat_index is not None:
            raise ValueError("send range OR beat_index (or neither, for the scene)")
        return self


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


async def _beat_text(db: AsyncSession, scene_id: UUID, beat_index: int) -> str:
    beat = (
        await db.execute(
            select(SceneBeatORM).where(
                SceneBeatORM.scene_id == scene_id,
                SceneBeatORM.beat_number == beat_index,
            )
        )
    ).scalar_one_or_none()
    if beat is None or not (beat.content or "").strip():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Beat not found"
        )
    return beat.content


async def revise_estimate(db: AsyncSession, scene: SceneORM, span: rv.Span) -> tuple:
    """(estimate, canon) for revising `span` — the numbers the estimate endpoint
    shows are the numbers this endpoint enforces (R7.5)."""
    from app.core import estimates
    from app.orchestration.prose import scene_cast
    from app.planning.canon import build_canon, render_character

    canon = await build_canon(db, scene.book_id)
    cast = scene_cast(canon, revise_job.cast_request(scene))
    draft = revise_job.draft_of(scene)
    est = estimates.revise(
        canon_chars=len(canon.full_block()),
        cast_context_chars=sum(len(render_character(c)) + 1500 for c in cast),
        draft_chars=len(draft),
        target_chars=span.end - span.start,
    )
    return est, canon


def resolve_request_span(
    draft: str, payload_range, beat_text: Optional[str] = None
) -> rv.Span:
    try:
        return rv.resolve_span(
            draft,
            range_=(payload_range.start, payload_range.end) if payload_range else None,
            beat_text=beat_text,
        )
    except rv.SpanError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


def _proposal_view(row: SceneRevisionORM, draft_hash: str) -> dict:
    meta = row.meta or {}
    return {
        "id": str(row.id),
        "scene_id": str(row.scene_id),
        "content": row.content,
        "word_count": row.word_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "instruction": meta.get("instruction"),
        "range": meta.get("range"),
        "revised_range": meta.get("revised_range"),
        "beat_index": meta.get("beat_index"),
        "job_id": meta.get("job_id"),
        "status": meta.get("status"),
        "warnings": meta.get("warnings") or [],
        # Written against a draft that has since changed: accept would 409.
        "stale": meta.get("base_text_hash") != draft_hash,
    }


@router.post("/scenes/{scene_id}/revise", response_model=dict)
async def revise_scene(
    scene_id: UUID,
    payload: ReviseRequest,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask for an AI revision of a range, a beat, or the whole scene. Returns
    the job; its result arrives as a proposal (GET .../proposals)."""
    scene = await owned_scene(scene_id, current_user, db)
    dedupe_key = f"revise:{scene.id}"

    # One revision in flight per scene: a repeat returns the running job.
    existing = await jobs_repo.find_active(db, dedupe_key)
    if existing is not None:
        return {
            "scene_id": str(scene.id),
            "job_id": str(existing.id),
            "status": "processing",
            "deduplicated": True,
        }
    if scene.status in _GENERATING:
        raise _conflict("The scene is still being generated; revise it once done.")

    draft = revise_job.draft_of(scene)
    draft_hash = rv.text_hash(draft)
    if payload.base_text_hash is not None and payload.base_text_hash != draft_hash:
        raise _conflict("The draft changed since you loaded it; reload and retry.")
    beat_text = None
    if payload.beat_index is not None:
        beat_text = await _beat_text(db, scene.id, payload.beat_index)
    span = resolve_request_span(draft, payload.range, beat_text)
    if payload.selected_text is not None and (
        draft[span.start : span.end] != payload.selected_text
    ):
        raise _conflict(
            "The selected text does not match the draft at that range; reload "
            "the scene and select again."
        )

    await check_user_budget(db, current_user.id)
    from app.core import estimates

    est, _ = await revise_estimate(db, scene, span)
    assessed = await estimates.assess(db, current_user.id, est)
    if not assessed["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"message": assessed["reason"], "estimate": assessed},
        )

    job_payload = {
        "scene_id": str(scene.id),
        "user_id": str(current_user.id),
        "book_id": str(scene.book_id),
        "instruction": payload.instruction,
        "range": [span.start, span.end],
        "beat_index": payload.beat_index,
        "base_text_hash": draft_hash,
    }
    job = await jobs_repo.enqueue(
        db,
        kind=revise_job.KIND,
        payload=job_payload,
        user_id=current_user.id,
        max_attempts=1,  # a retry re-spends; the author can ask again
        dedupe_key=dedupe_key,
        domain=("scene", scene.id),
    )
    # Two requests racing past the lookup above: the loser gets the winner's job.
    deduplicated = job.payload != job_payload
    job_id = str(job.id)
    await db.commit()
    return {
        "scene_id": str(scene_id),
        "job_id": job_id,
        "status": "processing",
        "deduplicated": deduplicated,
        "range": [span.start, span.end],
        "estimate": assessed,
    }


@router.get("/scenes/{scene_id}/proposals", response_model=dict)
async def list_proposals(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The scene's AI revision proposals, newest first, with the current draft
    and its hash (what a range is counted against)."""
    scene = await owned_scene(scene_id, current_user, db)
    draft = revise_job.draft_of(scene)
    draft_hash = rv.text_hash(draft)
    rows = (
        (
            await db.execute(
                select(SceneRevisionORM)
                .where(
                    SceneRevisionORM.scene_id == scene.id,
                    SceneRevisionORM.source == "ai_revise",
                )
                .order_by(SceneRevisionORM.created_at.desc(), SceneRevisionORM.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "scene_id": str(scene.id),
        "draft": draft,
        "draft_hash": draft_hash,
        "proposals": [_proposal_view(r, draft_hash) for r in rows],
    }


async def _owned_proposal(
    db: AsyncSession, scene: SceneORM, revision_id: UUID
) -> SceneRevisionORM:
    row = (
        await db.execute(
            select(SceneRevisionORM)
            .where(
                SceneRevisionORM.id == revision_id,
                SceneRevisionORM.scene_id == scene.id,
                SceneRevisionORM.source == "ai_revise",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found"
        )
    current = (row.meta or {}).get("status")
    if current != "proposed":
        raise _conflict(f"This proposal is already {current}.")
    return row


@router.post("/scenes/{scene_id}/revisions/{revision_id}/accept", response_model=dict)
async def accept_proposal(
    scene_id: UUID,
    revision_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Make the proposal the draft. The current draft is snapshotted first, as a
    manual save would; 409 if the draft changed since the proposal was written
    (its untouched text would no longer match)."""
    scene = await owned_scene(scene_id, current_user, db)
    row = await _owned_proposal(db, scene, revision_id)
    meta = row.meta or {}
    previous = revise_job.draft_of(scene)
    if rv.text_hash(previous) != meta.get("base_text_hash"):
        raise _conflict(
            "The draft changed after this revision was written; ask for a new "
            "revision of the current text."
        )

    # Same snapshot rule as update_scene_content (app/api/books.py).
    if previous:
        db.add(
            SceneRevisionORM(
                scene_id=scene.id,
                content=previous,
                word_count=len(previous.split()),
                source="edited" if scene.content else "generated",
            )
        )
    now = datetime.now(timezone.utc)
    scene.content = row.content
    scene.word_count = len(row.content.split())
    scene.updated_at = now
    row.meta = {**meta, "status": "accepted", "decided_at": now.isoformat()}
    await db.commit()
    return {
        "id": str(scene.id),
        "revision_id": str(row.id),
        "status": "accepted",
        "word_count": scene.word_count,
        "updated_at": scene.updated_at.isoformat(),
    }


@router.post("/scenes/{scene_id}/revisions/{revision_id}/discard", response_model=dict)
async def discard_proposal(
    scene_id: UUID,
    revision_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Discard a proposal. The draft is not touched; the proposal stays in the
    history, marked discarded."""
    scene = await owned_scene(scene_id, current_user, db)
    row = await _owned_proposal(db, scene, revision_id)
    row.meta = {
        **(row.meta or {}),
        "status": "discarded",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.commit()
    return {"id": str(scene.id), "revision_id": str(row.id), "status": "discarded"}
