"""The `revise_scene` job (SPEC-19, backlog B3): one AI revision of a scene span.

Enqueued by `POST /books/scenes/{id}/revise` (app/api/revise.py) with the span
already resolved to a character range of the draft and the draft's hash. The
run re-reads the scene, refuses if the draft changed since the request (the
range would point at different text), makes one model call and stores the
spliced result as a proposal: `SceneRevision(source="ai_revise")` with
`meta.status = "proposed"`. The draft itself is never written here — only
accepting the proposal changes it.

Lifecycle (SPEC-07): the domain row is the scene, whose status says whether its
GENERATION finished, so pause/resume leave it alone (a revision in flight does
not make a completed scene "paused"). Dead and cancelled jobs mark their
proposal, if one was stored, failed / cancelled. QuotaExhaustedError is not
caught, so the worker pauses the job (R7.2).
"""

from uuid import UUID

from sqlalchemy import select

from app.core.database import get_async_session
from app.core.logging_config import log_business_event, setup_logging
from app.core.orm_models import Scene, SceneBeat, SceneRevision
from app.jobs.context import current_job
from app.orchestration.revise import JOB_KIND

logger = setup_logging("jobs.revise")

KIND = JOB_KIND
DRAFT_EDITED = (
    "The draft was edited after this revision was requested, so the selected "
    "range no longer points at the same text. Request the revision again."
)


class RevisionConflict(RuntimeError):
    """The scene moved on underneath the job; the message is the job's error."""


async def run(payload: dict) -> None:
    from app.characters.context import build_cast_context
    from app.orchestration import revise as rv
    from app.orchestration.prose import scene_setup

    scene_id = UUID(payload["scene_id"])
    book_id = UUID(payload["book_id"])
    user_id = UUID(payload["user_id"])
    base_hash = payload["base_text_hash"]
    instruction = payload["instruction"]

    async with get_async_session() as session:
        scene = await session.get(Scene, scene_id)
        if scene is None or scene.deleted_at is not None:
            raise RevisionConflict("The scene was deleted.")
        draft = scene.draft
        request = rv.cast_request(scene)
        setup = await scene_setup(session, book_id, request, scene.id)
        beats = (
            await session.execute(
                select(SceneBeat.beat_number, SceneBeat.description)
                .where(SceneBeat.scene_id == scene_id)
                .order_by(SceneBeat.beat_number)
            )
        ).all()
        lines = [line for _, line in beats]
        redo, later = payload.get("beat_index"), []
        if redo is not None and scene.chapter_id is not None:
            later = await session.scalars(  # the chapter's live later scenes
                select(Scene)
                .where(
                    Scene.chapter_id == scene.chapter_id,
                    Scene.status == "completed",
                    Scene.position > scene.position,
                    Scene.deleted_at.is_(None),
                )
                .order_by(Scene.position)
            )
        later = [s.draft for s in later]
    canon = setup.canon
    if rv.text_hash(draft) != base_hash:
        raise RevisionConflict(DRAFT_EDITED)

    span = rv.Span(*payload["range"])
    prior, target, after = span.split(draft)
    # SPEC-41: no movement sample restates this scene's beats (B-1); a redo
    # keeps what the rest of the chapter refers to.
    scope = setup.scope.planned(filter(None, lines))
    rest = "\n\n".join((prior, after, *later))
    keep = rv.callbacks(target, rest, canon) if redo is not None else []
    ctx = current_job()
    revised = (ctx.checkpoint or {}).get("revised")
    if revised is None:
        cast_context = await build_cast_context(
            setup.cast, f"{instruction}\n{target}", scope=scope
        )
        prompt = rv.build_revise_prompt(
            canon_block=canon.full_block(),
            chapter_summary=setup.chapter_summary,
            cast_context=cast_context,
            prior=prior,
            target=target,
            after=after,
            instruction=instruction,
            pov=setup.pov,
            story_position=setup.position,
            secondary_block=setup.secondary_block,
            # SPEC-47: the beat the API located by its number, not a position.
            beat=(
                ([n for n, _ in beats].index(redo), lines) if redo is not None else None
            ),
            keep=[(t, rv.callback_context(t, rest)) for t in keep],
        )
        revised = await rv.generate_revision(
            prompt, target_chars=len(target), user_id=user_id
        )
        # The checkpoint is the cancel seam: a cancel asked during the call
        # stops here, before anything is stored.
        await ctx.save(
            {"revised": revised, "progress": {"stage": "revise", "done": 1, "total": 1}}
        )

    proposal = rv.splice(draft, span, revised)
    unknown = rv.new_names(revised, draft, canon)
    warnings = (
        [
            "The revision introduces names not in the canon or the scene "
            "(may be inventions): " + ", ".join(unknown[:12])
        ]
        if unknown
        else []
    )
    dropped = rv.dropped_callbacks(revised, keep)
    if dropped:
        quoted = ", ".join(f"'{t}'" for t in dropped)
        warnings.append(rv.DROPPED_CALLBACK.format(terms=quoted))

    async with get_async_session() as session:
        scene = await session.get(Scene, scene_id)
        if scene is None or scene.deleted_at is not None:
            raise RevisionConflict("The scene was deleted.")
        if rv.text_hash(scene.draft) != base_hash:
            raise RevisionConflict(DRAFT_EDITED)
        row = SceneRevision(
            scene_id=scene_id,
            content=proposal,
            word_count=len(proposal.split()),
            source="ai_revise",
            meta={
                "instruction": instruction,
                "range": [span.start, span.end],
                "revised_range": [span.start, len(proposal) - len(after)],
                "beat_index": payload.get("beat_index"),
                "job_id": str(ctx.job_id) if ctx.job_id else None,
                "status": "proposed",
                "base_text_hash": base_hash,
                "warnings": warnings,
                "unknown_names": unknown,
                "dropped_callbacks": dropped,
            },
        )
        session.add(row)
        await session.commit()
        revision_id = str(row.id)
    log_business_event(
        logger,
        "scene_revision_proposed",
        scene_id=str(scene_id),
        revision_id=revision_id,
        warnings=len(warnings),
    )


async def _mark_proposal(payload: dict, status: str) -> None:
    """Settle this job's stored proposal (if any) as failed / cancelled. Only a
    still-`proposed` one: an author's accept or discard is never overwritten."""
    job_id = current_job().job_id
    if not payload.get("scene_id") or job_id is None:
        return
    async with get_async_session() as session:
        rows = (
            (
                await session.execute(
                    select(SceneRevision).where(
                        SceneRevision.scene_id == UUID(payload["scene_id"]),
                        SceneRevision.source == "ai_revise",
                    )
                )
            )
            .scalars()
            .all()
        )
        changed = False
        for row in rows:
            meta = row.meta or {}
            if meta.get("job_id") == str(job_id) and meta.get("status") == "proposed":
                row.meta = {**meta, "status": status}
                changed = True
        if changed:
            await session.commit()
            log_business_event(
                logger, f"scene_revision_{status}", scene_id=payload["scene_id"]
            )


async def on_dead(payload: dict) -> None:
    await _mark_proposal(payload, "failed")


async def on_cancel(payload: dict) -> None:
    await _mark_proposal(payload, "cancelled")


async def on_pause(payload: dict) -> None:
    """The scene's status is about its generation; a paused revision leaves it
    (and the draft) alone. The job API reports the pause and resume time."""


async def on_resume(payload: dict) -> None:
    """See on_pause."""
