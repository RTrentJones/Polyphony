"""Enqueue a Source -> proposed Canon extraction run (shared by upload + /extract).

Centralized so upload, reprocess, and the explicit /extract endpoint all create
canon the SAME way: as reviewable proposals, never a direct write (docs/BRD.md
R4.4). The reviewed commit is the only path that writes canon.
"""

from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import ExtractionRun
from app.jobs import repository as jobs_repo
from app.parsing.extraction_plan import preflight_extraction


class ExtractionTooLarge(Exception):
    """The preflight refused: the run cannot finish within the budget window."""

    def __init__(self, preflight: dict):
        super().__init__(preflight.get("message", "extraction too large"))
        self.preflight = preflight


def too_large_http(e: ExtractionTooLarge) -> HTTPException:
    """The refusal as an API error, carrying the estimate and the alternative."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"message": str(e), "preflight": e.preflight},
    )


async def enqueue_extraction(
    db: AsyncSession,
    *,
    book_id: UUID,
    source_id: UUID,
    user_id: UUID,
    text: Optional[str] = None,
    max_chapters: Optional[int] = None,
) -> ExtractionRun:
    """Preflight, then create a pending ExtractionRun and enqueue the
    extract_canon job. No commit.

    With `text`, the cost is estimated first (SPEC-09) and ExtractionTooLarge is
    raised — nothing created — when it cannot finish; the estimate rides on the
    returned run as `run.preflight`.
    """
    preflight = None
    if text is not None:
        preflight = await preflight_extraction(
            db, user_id, text, max_chapters=max_chapters
        )
        if not preflight["fits"]:
            raise ExtractionTooLarge(preflight)
    run = ExtractionRun(
        book_id=book_id,
        source_id=source_id,
        user_id=user_id,
        status="pending",
        progress=(
            {"phase": "queued", "windows_total": preflight["windows"]}
            if preflight
            else None
        ),
    )
    db.add(run)
    await db.flush()
    payload = {
        "run_id": str(run.id),
        "source_id": str(source_id),
        "user_id": str(user_id),
    }
    if max_chapters:
        payload["max_chapters"] = int(max_chapters)
    await jobs_repo.enqueue(
        db,
        kind="extract_canon",
        payload=payload,
        user_id=user_id,
        max_attempts=2,
    )
    run.preflight = preflight  # transient, for the enqueueing endpoint's response
    return run
