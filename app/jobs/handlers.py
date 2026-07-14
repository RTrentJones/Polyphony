"""Job-kind dispatch table.

Each handler runs a workflow entrypoint from a job payload and raises
JobExecutionError on failure so the job records the attempt. The workflows
swallow their own exceptions (they mark the domain row failed and return a
status dict), so handlers re-raise based on that reported status.

on_dead is the crash-path guarantee: it runs when a job dies without its
workflow error handling ever completing (worker killed mid-job, attempts
exhausted by the reaper) and flips the domain row out of 'processing' so
nothing is stuck forever.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.logging_config import log_business_event, setup_logging
from app.core.orm_models import ContinuityReport, Manuscript, Scene

logger = setup_logging("jobs.handlers")


class JobExecutionError(RuntimeError):
    """A job attempt failed; the message is stored on the job row."""


@dataclass(frozen=True)
class Handler:
    run: Callable[[dict], Awaitable[None]]
    on_dead: Callable[[dict], Awaitable[None]] | None = None


async def _run_generate_scene(payload: dict) -> None:
    from app.orchestration.workflow import run_scene_workflow

    result = await run_scene_workflow(
        UUID(payload["scene_id"]), payload["request"], UUID(payload["user_id"])
    )
    if result.get("status") == "failed":
        raise JobExecutionError(result.get("error", "scene generation failed"))


async def _run_generate_prose_scene(payload: dict) -> None:
    from app.orchestration.prose import run_prose_scene_workflow

    book_id = payload.get("book_id")
    result = await run_prose_scene_workflow(
        UUID(payload["scene_id"]),
        payload["request"],
        UUID(payload["user_id"]),
        chapter_summary=payload.get("chapter_summary", ""),
        prior_scene_tail=payload.get("prior_tail", ""),
        book_id=UUID(book_id) if book_id else None,
    )
    if result.get("status") == "failed":
        raise JobExecutionError(result.get("error", "prose scene generation failed"))


async def _run_process_manuscript(payload: dict) -> None:
    from app.parsing.pipeline import process_manuscript

    manuscript_id = UUID(payload["manuscript_id"])
    # text=None: the pipeline reads the durable content_text from the row.
    await process_manuscript(manuscript_id, UUID(payload["user_id"]))
    # The pipeline marks the row failed instead of raising; surface that to
    # the job so retries/backoff apply.
    async with get_async_session() as session:
        status = (
            await session.execute(
                select(Manuscript.status).where(Manuscript.id == manuscript_id)
            )
        ).scalar_one_or_none()
    if status == "failed":
        raise JobExecutionError("manuscript processing failed")


async def _run_continuity(payload: dict) -> None:
    # Lazy imports: plans (router module) imports app.jobs.repository, so
    # importing it at module scope here would be a cycle.
    from app.api.plans import _build_fact_sheet, _collect_prose
    from app.core.orm_models import Book
    from app.planning.continuity import run_continuity_check

    report_id = UUID(payload["report_id"])
    async with get_async_session() as session:
        report = await session.get(ContinuityReport, report_id)
        if report is None:
            return  # deleted since enqueue; nothing to do
        book = await session.get(Book, report.book_id)
        if book is None:
            return
        prose = await _collect_prose(report.book_id, report.chapter_id, session)
        fact_sheet = await _build_fact_sheet(book, session)

    # LLM work happens outside any session. An exception here fails the job;
    # retry/backoff and the on_dead report flip are the worker's business.
    findings, tokens = await run_continuity_check(
        prose, fact_sheet, UUID(payload["user_id"])
    )

    async with get_async_session() as session:
        report = await session.get(ContinuityReport, report_id)
        if report is not None:
            report.findings = findings
            report.tokens_used = tokens
            report.status = "completed"
            await session.commit()


async def _fail_row(model, row_id: str, event: str) -> None:
    """Flip a domain row to 'failed' if it is still 'processing'."""
    async with get_async_session() as session:
        row = (
            await session.execute(select(model).where(model.id == UUID(row_id)))
        ).scalar_one_or_none()
        if row is not None and row.status == "processing":
            row.status = "failed"
            await session.commit()
            log_business_event(logger, event, id=row_id)


async def _dead_scene(payload: dict) -> None:
    await _fail_row(Scene, payload["scene_id"], "scene_failed_dead_job")


async def _dead_manuscript(payload: dict) -> None:
    await _fail_row(Manuscript, payload["manuscript_id"], "manuscript_failed_dead_job")


async def _dead_report(payload: dict) -> None:
    await _fail_row(ContinuityReport, payload["report_id"], "report_failed_dead_job")


HANDLERS: dict[str, Handler] = {
    "generate_scene": Handler(run=_run_generate_scene, on_dead=_dead_scene),
    "generate_prose_scene": Handler(run=_run_generate_prose_scene, on_dead=_dead_scene),
    "process_manuscript": Handler(
        run=_run_process_manuscript, on_dead=_dead_manuscript
    ),
    "continuity_check": Handler(run=_run_continuity, on_dead=_dead_report),
}
