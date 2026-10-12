"""Job-kind dispatch table.

Each handler runs a workflow entrypoint from a job payload and raises
JobExecutionError on failure so the job records the attempt. The workflows
swallow their own exceptions (they mark the domain row failed and return a
status dict), so handlers re-raise based on that reported status.

on_dead is the crash-path guarantee: it runs when a job dies without its
workflow error handling ever completing (worker killed mid-job, attempts
exhausted by the reaper, a pause that outlived JOB_MAX_PAUSE_HOURS) and flips
the domain row out of 'processing' so nothing is stuck forever.

Every kind mirrors its whole lifecycle onto its domain row (SPEC-07): on_pause
(-> 'paused'), on_resume (-> in progress again), on_cancel (-> 'cancelled', or a
plan back to its prior state) and on_dead (-> 'failed'). Handlers read their
job's checkpoint and save callback from `app.jobs.context.current_job()`, so a
resumed job continues from its last completed model call (SPEC-08).
"""

from dataclasses import dataclass
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.logging_config import log_business_event, setup_logging
from app.core.orm_models import ContinuityReport, Scene
from app.jobs.context import current_job

logger = setup_logging("jobs.handlers")


class JobExecutionError(RuntimeError):
    """A job attempt failed; the message is stored on the job row."""


@dataclass(frozen=True)
class Handler:
    run: Callable[[dict], Awaitable[None]]
    on_dead: Callable[[dict], Awaitable[None]] | None = None
    # Runs when the worker PAUSES the job (quota exhausted) — a chance to reflect
    # the pause on the domain row so the UI shows "paused", not "still generating".
    on_pause: Callable[[dict], Awaitable[None]] | None = None
    # Runs when the worker claims a previously paused job, before the run.
    on_resume: Callable[[dict], Awaitable[None]] | None = None
    # Runs when the author cancels the job (queued, paused, or at a seam).
    on_cancel: Callable[[dict], Awaitable[None]] | None = None


async def _scene_inputs(scene_id: UUID, payload: dict) -> tuple[str, str]:
    """The chapter summary and prior-scene tail, recomputed from the DB at start.

    Derived data goes stale across a pause (the author edits the chapter summary
    or finishes the previous scene meanwhile), so it is not carried in the
    payload (SPEC-08). Standalone scenes fall back to what the payload had.
    """
    from app.core.orm_models import Chapter
    from app.exports.builder import scene_text
    from app.orchestration.prose import PREV_TAIL_WORDS

    async with get_async_session() as session:
        scene = await session.get(Scene, scene_id)
        if scene is None or scene.chapter_id is None:
            return payload.get("chapter_summary", ""), payload.get("prior_tail", "")
        chapter = await session.get(Chapter, scene.chapter_id)
        summary = (chapter.summary or "") if chapter is not None else ""
        prev = (
            await session.execute(
                select(Scene)
                .where(
                    Scene.chapter_id == scene.chapter_id,
                    Scene.status == "completed",
                    Scene.position < scene.position,
                )
                .order_by(Scene.position.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        tail = ""
        if prev is not None:
            tail = " ".join(scene_text(prev).split()[-PREV_TAIL_WORDS:])
        return summary, tail


async def _run_generate_prose_scene(payload: dict) -> None:
    from app.orchestration.prose import run_prose_scene_workflow

    book_id = payload.get("book_id")
    ctx = current_job()
    scene_id = UUID(payload["scene_id"])
    chapter_summary, prior_tail = await _scene_inputs(scene_id, payload)
    result = await run_prose_scene_workflow(
        scene_id,
        payload["request"],
        UUID(payload["user_id"]),
        chapter_summary=chapter_summary,
        prior_scene_tail=prior_tail,
        book_id=UUID(book_id) if book_id else None,
        checkpoint=ctx.checkpoint,
        save_checkpoint=ctx.save,
        on_restart=ctx.note,
    )
    if result.get("status") == "failed":
        raise JobExecutionError(result.get("error", "prose scene generation failed"))


async def _run_generate_ensemble_scene(payload: dict) -> None:
    from app.orchestration.ensemble import run_ensemble_scene_workflow

    book_id = payload.get("book_id")
    ctx = current_job()
    scene_id = UUID(payload["scene_id"])
    chapter_summary, prior_tail = await _scene_inputs(scene_id, payload)
    result = await run_ensemble_scene_workflow(
        scene_id,
        payload["request"],
        UUID(payload["user_id"]),
        book_id=UUID(book_id) if book_id else None,
        chapter_summary=chapter_summary,
        prior_scene_tail=prior_tail,
        checkpoint=ctx.checkpoint,
        save_checkpoint=ctx.save,
        on_restart=ctx.note,
    )
    if result.get("status") == "failed":
        raise JobExecutionError(result.get("error", "ensemble scene generation failed"))


async def _run_index_characters_voice(payload: dict) -> None:
    from app.parsing.pipeline import index_characters_voice

    # Explicit approved character IDs (not source_id rediscovery), so merged
    # existing characters are indexed too. Idempotent + retryable per source.
    await index_characters_voice(
        UUID(payload["source_id"]),
        UUID(payload["book_id"]),
        UUID(payload["user_id"]),
        payload.get("character_ids", []),
    )


async def _run_generate_outline(payload: dict) -> None:
    """Outline as a background job (Phase 5; SPEC-07 for mode="single").

    Builds the canon (S0), runs S1-S4 writing BookPlan.stage between stages for
    progress, then persists the result + a version. QuotaExhaustedError from any
    stage propagates so the worker PAUSES the job (free tier); on resume the
    staged run continues from its last completed stage (SPEC-08).

    mode="single" is the degraded one-call outline (or a beat sheet). It used to
    run inside the HTTP request, where 429 backoff could outlive the proxy's
    timeout (F13); as a job it pauses and resumes like everything else.

    Premise checkpoint (SPEC-10): with `confirm_premise` the job runs S1 only,
    stores the skeleton on the plan, leaves it `awaiting_confirmation` and
    SUCCEEDS — nothing is held open while the author reads. Confirming
    enqueues a continuation job (`premise_confirmed`) that reads the confirmed
    skeleton back from the plan and runs S2-S4 with it as authoritative.
    """
    from app.core.orm_models import BookPlan
    from app.planning.canon import build_canon
    from app.planning.outline import generate_outline
    from app.planning.staged_outline import generate_premise, generate_staged_outline
    from app.versioning import repository as versions_repo

    plan_id = UUID(payload["plan_id"])
    book_id = UUID(payload["book_id"])
    user_id = UUID(payload["user_id"])
    chapters_target = int(payload.get("chapters_target", 12))

    async with get_async_session() as session:
        canon = await build_canon(session, book_id)

    async def on_stage(stage: str) -> None:
        async with get_async_session() as session:
            plan = await session.get(BookPlan, plan_id)
            if plan is not None:
                plan.status = "generating"
                plan.stage = stage
                await session.commit()

    async def store_premise(skeleton: dict, status: str | None = None) -> None:
        async with get_async_session() as session:
            plan = await session.get(BookPlan, plan_id)
            if plan is None:
                return
            plan.premise = {
                "premise_restated": str(skeleton.get("premise_restated") or ""),
                "central_conflict": str(skeleton.get("central_conflict") or ""),
                "acts": skeleton.get("acts") or [],
                "confirmed": bool(skeleton.get("confirmed")),
                "chapters_target": chapters_target,
            }
            if status is not None:
                plan.status = status
                plan.stage = None
                plan.error = None
            await session.commit()

    confirmed_skeleton = None
    if payload.get("premise_confirmed"):
        async with get_async_session() as session:
            plan = await session.get(BookPlan, plan_id)
            confirmed_skeleton = dict((plan.premise if plan else None) or {})
        confirmed_skeleton["confirmed"] = True

    ctx = current_job()
    if payload.get("mode") != "single" and (
        payload.get("confirm_premise") and confirmed_skeleton is None
    ):
        skeleton = await generate_premise(
            canon,
            chapters_target=chapters_target,
            user_id=user_id,
            on_stage=on_stage,
            checkpoint=ctx.checkpoint,
            save_checkpoint=ctx.save,
        )
        await store_premise(skeleton, status="awaiting_confirmation")
        return
    if payload.get("mode") == "single":
        await on_stage("single")
        nodes = await generate_outline(
            title=canon.title,
            synopsis=canon.synopsis,
            genre=canon.genre or "",
            character_bible=canon.bible(),
            kind=payload.get("kind", "outline"),
            chapters_target=chapters_target,
            user_id=user_id,
        )
        warnings = None
    else:
        nodes, warnings = await generate_staged_outline(
            canon,
            chapters_target=chapters_target,
            user_id=user_id,
            on_stage=on_stage,
            checkpoint=ctx.checkpoint,
            save_checkpoint=ctx.save,
            on_restart=ctx.note,
            skeleton=confirmed_skeleton,
            on_premise=None if confirmed_skeleton else store_premise,
        )

    async with get_async_session() as session:
        plan = await session.get(BookPlan, plan_id)
        if plan is None:
            return
        plan.content = nodes
        plan.warnings = warnings
        plan.status = "ready"
        plan.stage = None
        plan.error = None
        await versions_repo.snapshot(
            session,
            book_id=book_id,
            entity_type="book_plan",
            entity_id=plan.id,
            content={"kind": plan.kind, "content": nodes, "premise": plan.premise},
            reason="generated",
            created_by=user_id,
        )
        await session.commit()


async def _run_extract_canon(payload: dict) -> None:
    """Extract a Source into proposed canon, held on the run for review (Phase 6).

    Writes proposals, never real entities. Map-reduce over ~12k-token windows
    (SPEC-09): every finished model call is checkpointed on the JOB (SPEC-08;
    the run's `progress` keeps only the summary the review screen shows), so a
    quota pause (QuotaExhaustedError propagates and the worker pauses) resumes
    at the next window instead of re-spending finished ones, and a cancel stops
    at the same seam.
    `max_chapters` (the preflight's "first N chapters" alternative) limits the
    run to the opening chapters the author chose.
    """
    from app.core.orm_models import ExtractionRun, Source
    from app.parsing.canon_extractor import extract_canon
    from app.parsing.extraction_plan import first_chapters

    run_id = UUID(payload["run_id"])
    source_id = UUID(payload["source_id"])
    user_id = UUID(payload["user_id"])

    async with get_async_session() as session:
        source = await session.get(Source, source_id)
        text = source.content_text if source is not None else ""
    if not text:
        async with get_async_session() as session:
            run = await session.get(ExtractionRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = "source has no stored content"
                await session.commit()
        return
    if payload.get("max_chapters"):
        text = first_chapters(text, int(payload["max_chapters"]))

    ctx = current_job()

    async def save_checkpoint(data: dict) -> None:
        # The summary the review screen shows lives on the run ...
        async with get_async_session() as session:
            row = await session.get(ExtractionRun, run_id)
            if row is not None:
                row.progress = {
                    "phase": data.get("phase"),
                    "windows_total": data.get("windows_total"),
                    "windows_done": data.get("windows_done"),
                    "reduce_total": data.get("reduce_total"),
                    "reduce_done": data.get("reduce_done"),
                }
                if row.status == "paused":
                    row.status = "pending"
                await session.commit()
        # ... the resume state is job-scoped, with the job API's progress.
        done = (data.get("windows_done") or 0) + (data.get("reduce_done") or 0)
        total = (data.get("windows_total") or 0) + (data.get("reduce_total") or 0)
        await ctx.save(
            {
                **data,
                "progress": {
                    "stage": data.get("phase") or "map",
                    "done": done,
                    "total": max(total, done),
                },
            }
        )

    checkpoint = dict(ctx.checkpoint or {})
    checkpoint.pop("progress", None)
    proposals = await extract_canon(
        text,
        user_id=user_id,
        checkpoint=checkpoint or None,
        save_checkpoint=save_checkpoint,
    )
    if payload.get("max_chapters"):
        proposals["stats"]["max_chapters"] = int(payload["max_chapters"])

    async with get_async_session() as session:
        run = await session.get(ExtractionRun, run_id)
        if run is not None:
            run.proposals = proposals
            run.status = "ready"
            run.error = None
            # The checkpoint has served its purpose; keep only the summary.
            run.progress = {
                "phase": "done",
                "windows_total": proposals["stats"].get("windows"),
                "windows_done": proposals["stats"].get("windows"),
            }
            await session.commit()


async def _run_continuity(payload: dict) -> None:
    # Lazy imports: plans (router module) imports app.jobs.repository, so
    # importing it at module scope here would be a cycle.
    from app.api.plans import _collect_prose
    from app.core.orm_models import Book
    from app.planning.continuity import build_fact_sheet, run_continuity_check

    report_id = UUID(payload["report_id"])
    async with get_async_session() as session:
        report = await session.get(ContinuityReport, report_id)
        if report is None:
            return  # deleted since enqueue; nothing to do
        book = await session.get(Book, report.book_id)
        if book is None:
            return
        if report.status == "paused":
            report.status = "processing"
            await session.commit()
        prose = await _collect_prose(report.book_id, report.chapter_id, session)
        fact_sheet = await build_fact_sheet(session, report.book_id)

    # LLM work happens outside any session. An exception here fails the job;
    # retry/backoff and the on_dead report flip are the worker's business.
    result = await run_continuity_check(prose, fact_sheet, UUID(payload["user_id"]))

    async with get_async_session() as session:
        report = await session.get(ContinuityReport, report_id)
        if report is not None:
            report.findings = result.findings
            report.tokens_used = result.tokens
            report.coverage = result.coverage
            # "incomplete" when a section could not be checked — never a
            # silent clean bill of health (SPEC-02).
            report.status = result.status
            await session.commit()


async def _move_row(
    model, row_id: str, from_statuses: tuple, to: str, event: str, **fields
) -> bool:
    """Move a domain row to `to` if it is currently in one of `from_statuses`.

    The guard makes every hook idempotent and stops a late hook from clobbering
    a row the author has since moved on (e.g. a completed scene)."""
    async with get_async_session() as session:
        row = (
            await session.execute(select(model).where(model.id == UUID(row_id)))
        ).scalar_one_or_none()
        if row is None or row.status not in from_statuses:
            return False
        row.status = to
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()
        log_business_event(logger, event, id=row_id)
        return True


async def _fail_row(model, row_id: str, event: str) -> None:
    """Flip a domain row to 'failed' if it is still in-flight ('processing' or a
    quota 'paused' state)."""
    await _move_row(model, row_id, ("processing", "paused"), "failed", event)


# --- scenes -------------------------------------------------------------------


async def _dead_scene(payload: dict) -> None:
    await _fail_row(Scene, payload["scene_id"], "scene_failed_dead_job")


async def _pause_scene(payload: dict) -> None:
    """Reflect a quota pause on the scene: 'processing' -> 'paused', so the UI can
    say "waiting for AI quota" instead of "still generating". The resumed run
    flips it back to 'completed'/'failed'; a repeated pause is a no-op."""
    await _move_row(
        Scene, payload["scene_id"], ("processing",), "paused", "scene_paused_quota"
    )


async def _resume_scene(payload: dict) -> None:
    await _move_row(
        Scene, payload["scene_id"], ("paused",), "processing", "scene_resumed"
    )


async def _cancel_scene(payload: dict) -> None:
    await _move_row(
        Scene,
        payload["scene_id"],
        ("processing", "paused"),
        "cancelled",
        "scene_cancelled",
    )


# --- continuity reports ---------------------------------------------------------


async def _dead_report(payload: dict) -> None:
    await _fail_row(ContinuityReport, payload["report_id"], "report_failed_dead_job")


async def _pause_report(payload: dict) -> None:
    await _move_row(
        ContinuityReport,
        payload["report_id"],
        ("processing",),
        "paused",
        "report_paused_quota",
    )


async def _resume_report(payload: dict) -> None:
    await _move_row(
        ContinuityReport,
        payload["report_id"],
        ("paused",),
        "processing",
        "report_resumed",
    )


async def _cancel_report(payload: dict) -> None:
    await _move_row(
        ContinuityReport,
        payload["report_id"],
        ("processing", "paused"),
        "cancelled",
        "report_cancelled",
    )


# --- extraction runs ------------------------------------------------------------


async def _dead_extraction(payload: dict) -> None:
    from app.core.orm_models import ExtractionRun

    await _move_row(
        ExtractionRun,
        payload["run_id"],
        ("pending", "processing", "paused"),
        "failed",
        "extraction_failed_dead_job",
        error=current_job().error or "extraction failed",
    )


async def _pause_extraction(payload: dict) -> None:
    from app.core.orm_models import ExtractionRun

    await _move_row(
        ExtractionRun,
        payload["run_id"],
        ("pending", "processing"),
        "paused",
        "extraction_paused_quota",
    )


async def _resume_extraction(payload: dict) -> None:
    from app.core.orm_models import ExtractionRun

    await _move_row(
        ExtractionRun, payload["run_id"], ("paused",), "pending", "extraction_resumed"
    )


async def _cancel_extraction(payload: dict) -> None:
    from app.core.orm_models import ExtractionRun

    await _move_row(
        ExtractionRun,
        payload["run_id"],
        ("pending", "processing", "paused"),
        "cancelled",
        "extraction_cancelled",
    )


# --- book plans -----------------------------------------------------------------


async def _dead_plan(payload: dict) -> None:
    from app.core.orm_models import BookPlan

    await _move_row(
        BookPlan,
        payload["plan_id"],
        ("generating", "paused"),
        "failed",
        "plan_failed_dead_job",
        stage=None,
        error=current_job().error or "outline generation failed",
    )


async def _pause_plan(payload: dict) -> None:
    from app.core.orm_models import BookPlan

    await _move_row(
        BookPlan, payload["plan_id"], ("generating",), "paused", "plan_paused_quota"
    )


async def _resume_plan(payload: dict) -> None:
    from app.core.orm_models import BookPlan

    await _move_row(
        BookPlan, payload["plan_id"], ("paused",), "generating", "plan_resumed"
    )


async def _cancel_plan(payload: dict) -> None:
    """Back to the plan's prior state when its previous content is intact (a
    generation never touches content until it succeeds); 'cancelled' for a plan
    that had nothing yet. A cancelled continuation goes back to waiting for the
    premise, which is still stored (SPEC-10)."""
    from app.core.orm_models import BookPlan

    async with get_async_session() as session:
        plan = await session.get(BookPlan, UUID(payload["plan_id"]))
        if plan is None or plan.status not in ("generating", "paused"):
            return
        prior = payload.get("prior_status")
        keep = plan.content or (prior == "awaiting_confirmation" and plan.premise)
        plan.status = prior if (prior and keep) else "cancelled"
        plan.stage = None
        plan.error = None
        await session.commit()
        log_business_event(logger, "plan_cancelled", id=payload["plan_id"])


async def _noop(payload: dict) -> None:
    """Voice indexing has no author-visible domain row to mirror."""


_SCENE_HOOKS = dict(
    on_dead=_dead_scene,
    on_pause=_pause_scene,
    on_resume=_resume_scene,
    on_cancel=_cancel_scene,
)

HANDLERS: dict[str, Handler] = {
    "generate_prose_scene": Handler(run=_run_generate_prose_scene, **_SCENE_HOOKS),
    "generate_ensemble_scene": Handler(
        run=_run_generate_ensemble_scene, **_SCENE_HOOKS
    ),
    "index_characters_voice": Handler(
        run=_run_index_characters_voice,
        on_dead=_noop,
        on_pause=_noop,
        on_resume=_noop,
        on_cancel=_noop,
    ),
    "generate_outline": Handler(
        run=_run_generate_outline,
        on_dead=_dead_plan,
        on_pause=_pause_plan,
        on_resume=_resume_plan,
        on_cancel=_cancel_plan,
    ),
    "extract_canon": Handler(
        run=_run_extract_canon,
        on_dead=_dead_extraction,
        on_pause=_pause_extraction,
        on_resume=_resume_extraction,
        on_cancel=_cancel_extraction,
    ),
    "continuity_check": Handler(
        run=_run_continuity,
        on_dead=_dead_report,
        on_pause=_pause_report,
        on_resume=_resume_report,
        on_cancel=_cancel_report,
    ),
}
