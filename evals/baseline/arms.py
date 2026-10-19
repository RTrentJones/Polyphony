"""W12-5 baseline harness (SPEC-84): per slot, B (prose) and C (ensemble) through
the API, and A in-process: `run_prose_scene_workflow` with B's beats and request,
`prose.build_cast_context` patched to A's cast (canon plus first lines). Every arm
runs under a job id; its cost is one SQL sum over api_usage.job_id.
"""

import json
import os
import random
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import bindparam, select, text

from app.characters.context import _later_plot, grounding_entries, render_character
from app.core.database import get_async_session
from app.core.llm_text import as_quoted_block
from app.core.orm_models import APIUsage, Character, Job, Scene, SceneBeat
from app.jobs.context import JobContext, job_context
from app.orchestration import prose
from app.parsing.character_extractor import SPEECH_MIN_WORDS, source_location
from app.rag.store import get_chunk_store
from evals.baseline import blind, score
from evals.steps.base import step
from evals.steps.pipeline import _book_with_cast, _outline_into

LINES = 3  # per present character (N3)
SLOTS = 2  # the dev dry run, cel x 2; W12-9 runs 18 slots (SPEC-84)


async def first_lines(character, scope, removed: set) -> list[dict]:
    """N3 (SPEC-84); `removed` collects what the later-plot cut dropped."""
    rows = await get_chunk_store().list_chunks(str(character.id), chunk_type="dialogue")
    rows.sort(
        key=lambda s: (s["source_id"] or "", source_location(s["source"]) or ("", 0))
    )
    out = []
    for s in (s for s in rows if len(s["text"].split()) >= SPEECH_MIN_WORDS):
        if len(out) == LINES:
            break
        if _later_plot(s, scope):
            removed.add(s["id"])
        else:
            out.append(s)
    return out


def a_cast_builder(removed: set):
    async def build(characters, beat_description, *, scope, grounding=None):
        blocks = []
        for c in characters:
            lines = await first_lines(c, scope, removed)
            if grounding is not None:
                grounding.extend(grounding_entries(c, lines))
            blocks.append(
                as_quoted_block(render_character(c.name, lines, []), "character")
            )
        return "\n\n".join(blocks)

    return build


def beats_from(grounding: dict, rows, names: dict) -> list[dict]:
    """B's beats, each cast rebuilt from its grounding entries, in cast order."""
    return [
        {
            "description": r.description,
            "characters": [
                names[UUID(c)]
                for c in dict.fromkeys(e["character_id"] for e in grounding[str(i)])
            ],
        }
        for i, r in enumerate(rows)
    ]


async def run_arm_a(b_scene_id: UUID) -> tuple[UUID, UUID, int]:
    """Arm A for B's slot: (scene id, job id, later_plot_removed)."""
    async with get_async_session() as db:
        b = await db.get(Scene, b_scene_id)
        b_job = await db.scalar(select(Job).where(Job.domain_id == b.id))
        who = select(Character.id, Character.name).where(Character.book_id == b.book_id)
        names = dict((await db.execute(who)).all())
        rows = await db.scalars(
            select(SceneBeat)
            .where(SceneBeat.scene_id == b.id)
            .order_by(SceneBeat.beat_number)
        )
        beats = beats_from(b.evaluation_scores["grounding"], rows, names)
        summary, position = await prose.scene_chapter(db, b)
        tail = await prose.scene_prior_tail(db, b)
        a = Scene(
            user_id=b.user_id,
            book_id=b.book_id,
            chapter_id=b.chapter_id,
            position=b.position,
            status="processing",
            scene_request=b.scene_request,
        )
        db.add(a)
        await db.flush()
        a_id, user_id, book_id = a.id, b.user_id, b.book_id
        request = b_job.payload["request"]  # exactly what B's job was given
        await db.commit()
    job_id, removed = uuid4(), set()
    seeded = {"beats": beats, "prose": []}  # B's beats: plan_scene_beats is skipped
    with job_context(JobContext(job_id=job_id)), patch.object(
        prose, "build_cast_context", a_cast_builder(removed)
    ):
        await prose.run_prose_scene_workflow(
            a_id,
            request,
            user_id,
            chapter_summary=summary,
            story_position=position,
            prior_scene_tail=tail,
            book_id=book_id,
            checkpoint=seeded,
        )
    return a_id, job_id, len(removed)


COST = "SELECT sum(cost_usd), sum(tokens_used), sum(CASE WHEN cost_usd IS NULL THEN tokens_used ELSE 0 END) FROM api_usage WHERE job_id = :j"


async def scene_record(slot: str, arm: str, scene_id, job_id=None, removed=None):
    """The frozen baseline scene record (tpm-plan-wave12 §1(b)); the arm's cost
    is one SQL sum over api_usage.job_id."""
    async with get_async_session() as db:
        scene = await db.get(Scene, UUID(str(scene_id)))
        job_id = job_id or await db.scalar(
            select(Job.id).where(Job.domain_id == scene.id)
        )
        cost_q = text(COST).bindparams(bindparam("j", type_=APIUsage.job_id.type))
        cost, tokens, unpriced = (await db.execute(cost_q, {"j": job_id})).one()
    cost = None if cost is None else float(cost)
    return {
        "slot": slot,
        "arm": arm,
        "scene_id": str(scene.id),
        "job_id": str(job_id),
        "latency_ms": scene.generation_time_ms,
        "tokens": tokens or 0,
        "cost_usd": cost,
        "unpriced_tokens": unpriced or 0,
        "text": scene.content or "",
        "later_plot_removed": removed,
    }


@step("baseline", needs_api=True)
async def step_baseline(ctx) -> dict:
    """The dry run (W12-5 (f)): labelled dev, never reported. Rater answers are
    synthetic; the bundle, sealed key and sheet go to EVAL baseline_dir."""
    from evals.harness.client import EvalClientError

    book = await _book_with_cast(ctx, f"eval-baseline-{ctx.book}", voice=True)
    await _outline_into(ctx, book)
    chapters = sorted(
        (await ctx.client.promote_all(book["id"]))["chapters"],
        key=lambda c: c.get("position") or 0,
    )
    records = []
    for n, ch in enumerate(chapters[:SLOTS]):
        detail = await ctx.client.get_chapter(ch["chapter_id"])
        beat = (detail.get("planned_beats") or [{}])[0]
        brief = {
            "character_ids": beat.get("character_ids") or detail.get("character_ids"),
            "scene_description": f"{beat.get('title')}. {beat.get('summary') or ''}",
            "setting": beat.get("setting") or "",
            "emotional_tone": beat.get("emotional_tone") or "",
            "target_word_count": 500,
            "planned_beat_id": beat.get("node_id"),
        }
        ids = {}
        for arm, mode in (("B", "prose"), ("C", "ensemble")):
            scene = await ctx.client.generate_scene(
                ch["chapter_id"], {**brief, "mode": mode}
            )
            if scene.get("mode_used") != mode:
                raise EvalClientError(
                    f"arm {arm} ran {scene.get('mode_used')} (set LLM_TIER=paid)"
                )
            ids[arm] = scene["id"]  # trashed: no arm reads another's prose
            await ctx.client.delete_scene(scene["id"])
        a_id, a_job, removed = await run_arm_a(UUID(ids["B"]))
        slot = f"{ctx.book}-{n + 1}"
        records += [await scene_record(slot, arm, sid) for arm, sid in ids.items()]
        records.append(await scene_record(slot, "A", a_id, a_job, removed))
    bundle, key = blind.blind(records, seed=random.SystemRandom().getrandbits(32))
    out = Path(os.getenv("EVAL_BASELINE_DIR", ".eval-baseline"))
    sealed = Path(os.getenv("EVAL_BASELINE_KEY", ".eval-baseline-key.json"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "bundle.json").write_text(json.dumps(bundle, indent=1))
    (out / "sheet.csv").write_text(blind.sheet(bundle))
    sealed.write_text(json.dumps(key))  # outside the bundle's directory
    rng = random.Random(0)  # synthetic rater answers: the pipeline, not a result
    prefs = [
        (p, rng.choice([*p, None])) for p in bundle["pairs"] for _rater in range(3)
    ]
    return {
        "label": "dev",
        "records": records,
        "score": score.score(records, blind.unblind(prefs, key)),
    }
