"""Ensemble scene loop: narrator / character agents / editor (Phase 8).

The app's namesake. A beat is drafted by an ENSEMBLE, not one prompt:

  * Character agents PROPOSE, one per character — that is where distinct voice is
    born (the polyphony). Each agent sees ONLY its own bio + the brief + its own
    retrieved chunks (~800 tok): a character does not know the plot, and giving
    every agent the whole canon costs ~2.5x for no fidelity gain (docs/BRD.md §8).
  * Actions are FIRST-CLASS. A proposal returns {intent, actions[], lines[],
    interiority}; actions must be non-empty — "your character may say nothing;
    what do they DO?" This inverts the old _generate_action, which derived action
    FROM dialogue post-hoc (exactly the wrong dependency).
  * The Narrator (full canon + beat) synthesizes the proposals into prose.
  * The Story editor (full canon + arc) reviews for arc fit, beat coverage, and
    that every present character actually acts — measured, not vibed.

Convergence is measured: zero blocking objections AND editor.satisfied AND
arc_fit >= threshold. Terminate on converged | max rounds | anti-oscillation
(round 2's objections not fewer than round 1's) | budget exhausted. Rounds and
agent count come from the Tier (free: 1 round, <=3 agents; paid: 2 rounds).
Non-convergence keeps the best draft with objections attached — never a hard
fail. Opt-in per scene; prose mode stays the default.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.characters.context import (
    CONTEXT_ONLY_REASON,
    SceneScope,
    build_agent_context,
    load_characters_for_book,
    unsampled_entry,
)
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_object
from app.llm.tier import get_tier
from app.orchestration.workflow import StoryPosition

logger = setup_logging("orchestration.ensemble")

ARC_FIT_THRESHOLD = 0.7


def _normalize_proposal(raw: dict, name: str) -> dict:
    """Coerce a character agent's reply; actions are required and non-empty."""
    actions = [str(a).strip() for a in (raw.get("actions") or []) if str(a).strip()]
    lines = [
        str(line).strip() for line in (raw.get("lines") or []) if str(line).strip()
    ]
    if not actions:
        # Actions are first-class: if the agent gave none, synthesize a minimal
        # beat of presence rather than let the character stand inert.
        actions = [f"{name} reacts to the moment."]
    return {
        "character": name,
        "intent": str(raw.get("intent", "") or "").strip(),
        "actions": actions,
        "lines": lines,
        "interiority": str(raw.get("interiority", "") or "").strip(),
    }


async def propose(
    character,
    name: str,
    beat_description: str,
    prior_objections: list[dict],
    user_id,
    *,
    scope: SceneScope,
    grounding: Optional[list] = None,
) -> dict:
    """One character agent's proposal — scoped to its OWN context only.

    `grounding`, when given, collects the voice chunks retrieved for this agent
    (character_id, chunk_id, score) — the same record the prose workflow keeps.
    """
    # How this character speaks (<voice>) and moves (<physicality>), retrieved
    # separately so dialogue cannot crowd action out (BRD R6.2, SPEC-14).
    context = await build_agent_context(
        character, name, beat_description, scope=scope, grounding=grounding
    )
    feedback = ""
    if prior_objections:
        notes = "; ".join(
            o.get("suggested_revision") or o.get("note", "")
            for o in prior_objections
            if o.get("character") in (name, None)
        )
        if notes:
            feedback = f"\nRevise per this feedback: {clean_for_llm(notes)}"

    prompt = f"""{STORY_MATERIAL_NOTICE}

You ARE {name}. You do not know the plot — only what {name} knows.

{context}

The moment: {clean_for_llm(beat_description)}{feedback}

Ground your ACTIONS in how {name} moves (<physicality>) and your LINES in how
{name} speaks (<voice>).

Your character may say NOTHING. What do they DO? Return ONLY a JSON object:
{{
  "intent": "what {name} wants in this moment",
  "actions": ["1-3 concrete physical actions/body language — REQUIRED, non-empty"],
  "lines": ["0-2 lines of dialogue in {name}'s voice, or []"],
  "interiority": "one line of private thought, optional"
}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=600,
        user_id=user_id,
        purpose="ensemble:proposal",
    )
    try:
        raw = extract_json_object(result.text)
    except ValueError:
        raw = {}
    return _normalize_proposal(raw, name)


async def narrate(
    proposals: list[dict],
    scene_request: dict,
    canon_context: str,
    prior_tail: str,
    user_id,
    present: Optional[list[str]] = None,
    chapter_summary: str = "",
) -> str:
    """Narrator: synthesize the proposals into prose (full canon + beat).

    `present` is the FULL cast of the beat. Characters with a proposal drive their
    own actions; any present character WITHOUT one (a context-only character over
    the tier's agent cap) must still be given something to do — never silently
    dropped (PR review #6).
    """
    proposed_names = {p["character"] for p in proposals}
    present = present or list(proposed_names)
    context_only = [n for n in present if n not in proposed_names]
    cast_block = "\n\n".join(
        f"{p['character']} — intent: {p['intent']}\n"
        f"  actions: {'; '.join(p['actions'])}\n"
        f"  lines: {'; '.join(p['lines']) or '(none)'}\n"
        f"  interiority: {p['interiority'] or '(none)'}"
        for p in proposals
    )
    context_note = (
        f"\nAlso present (give them something to DO, though they didn't propose): "
        f"{', '.join(context_only)}"
        if context_only
        else ""
    )
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_context}

{as_quoted_block(chapter_summary, "chapter")}

Setting: {clean_for_llm(str(scene_request.get("setting", "")))}
The scene so far (continue seamlessly from it):
{as_quoted_block(prior_tail, "prior") or "(this beat opens the scene)"}

Characters present in this beat: {", ".join(present)}

Each character has proposed how they act and speak in this beat:
{clean_for_llm(cast_block)}{context_note}

Weave these into a single continuous passage of prose. Honor each character's
actions AND lines — EVERY character present must visibly DO something, not merely
speak. Keep everyone in their established voice.

Prose:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=max(600, int(scene_request.get("target_word_count", 500) * 2)),
        user_id=user_id,
        purpose="ensemble:narration",
    )
    return result.text.strip()


async def editor_review(
    draft: str,
    canon_context: str,
    beat_description: str,
    present: list[str],
    user_id,
    chapter_summary: str = "",
) -> dict:
    """Story editor: measured review — arc fit, coverage, who actually acts."""
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_context}

{as_quoted_block(chapter_summary, "chapter")}

The beat to cover: {clean_for_llm(beat_description)}
It must serve this chapter's intent (above) as well as the book's arc.
Characters present: {", ".join(present)}

Review this draft:
{as_quoted_block(draft, "draft")}

Return ONLY a JSON object:
{{
  "satisfied": true/false,
  "arc_fit": 0.0-1.0,
  "beat_coverage": 0.0-1.0,
  "characters_acting": ["names who visibly ACT (not just speak) in the draft"],
  "objections": [{{"character": "name or null", "severity": "blocking|minor",
     "note": "what's wrong", "suggested_revision": "how to fix"}}]
}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1024,
        user_id=user_id,
        purpose="ensemble:review",
    )
    try:
        review = extract_json_object(result.text)
    except ValueError:
        review = {}
    # Enforce the actions-first contract: any present character the editor did not
    # see ACT becomes a blocking objection, so the next round must fix it.
    acting = {str(a) for a in (review.get("characters_acting") or [])}
    objections = list(review.get("objections") or [])
    for name in present:
        if name not in acting:
            objections.append(
                {
                    "character": name,
                    "severity": "blocking",
                    "note": f"{name} is present but does not visibly act",
                    "suggested_revision": f"Give {name} a concrete action.",
                }
            )
    review["objections"] = objections
    return review


def _blocking(objections: list[dict]) -> list[dict]:
    return [o for o in objections if o.get("severity") == "blocking"]


def _converged(review: dict) -> bool:
    return (
        bool(review.get("satisfied"))
        and float(review.get("arc_fit") or 0) >= ARC_FIT_THRESHOLD
        and not _blocking(review.get("objections") or [])
    )


async def run_ensemble(
    *,
    scene_request: dict,
    beat_description: str,
    user_id: UUID,
    book_id: UUID,
    canon_context: str,
    scope: SceneScope,
    prior_tail: str = "",
    state: Optional[dict] = None,
    save_state=None,
    chapter_summary: str = "",
) -> dict:
    """Run the ensemble loop for one beat. Returns {prose, evaluation, rounds}.

    Tier-gated: rounds and agent count come from the Tier; free tier is 1 round,
    <=3 proposal agents. Characters over that cap are context-only — they still
    appear in narration AND editor validation, never silently dropped (PR review
    #6). Convergence is measured; non-convergence keeps the best draft with
    objections (never a hard fail).

    `state`/`save_state` checkpoint the beat after every model call — each
    agent's proposal, the narration, the review (SPEC-08) — so a resumed beat
    makes only the calls it had not finished.
    """
    tier = get_tier()
    present = list(dict.fromkeys(scene_request.get("characters") or []))  # dedup, order
    agents = present[: tier.max_agents]  # only these get their own proposal agent

    # The whole present cast is loaded: agents get their rows, and context-only
    # characters get a grounding entry saying why no sample grounded them.
    characters = await load_characters_for_book(
        present, user_id=user_id, book_id=book_id
    )

    st = dict(state or {})
    prior_objections: list[dict] = list(st.get("prior_objections") or [])
    best: Optional[dict] = st.get("best")
    start_round = int(st.get("round") or 1)

    async def _checkpoint() -> None:
        if save_state is not None:
            await save_state(st)

    round_no = start_round
    for round_no in range(start_round, tier.max_rounds + 1):
        st["round"] = round_no
        # Each round re-retrieves; the kept draft's grounding is what it was
        # actually written from, so it travels with the candidate — and with
        # the checkpoint, so a resumed round keeps what its proposals retrieved.
        round_grounding: list = list(st.get("grounding") or [])
        proposals: list[dict] = list(st.get("proposals") or [])
        for name in agents[len(proposals) :]:
            proposals.append(
                await propose(
                    characters.get(name),
                    name,
                    beat_description,
                    prior_objections,
                    user_id,
                    scope=scope,
                    grounding=round_grounding,
                )
            )
            st["proposals"] = proposals
            st["grounding"] = round_grounding
            await _checkpoint()
        draft = st.get("draft")
        if draft is None:
            draft = await narrate(
                proposals,
                scene_request,
                canon_context,
                prior_tail,
                user_id,
                present=present,
                chapter_summary=chapter_summary,
            )
            st["draft"] = draft
            await _checkpoint()
        # Editor validates the FULL present cast, so a context-only character who
        # vanished is caught as a blocking objection, not reported as converged.
        review = st.get("review")
        if review is None:
            review = await editor_review(
                draft,
                canon_context,
                beat_description,
                present,
                user_id,
                chapter_summary=chapter_summary,
            )
            st["review"] = review
            await _checkpoint()
        candidate = {
            "prose": draft,
            "evaluation": review,
            "rounds": round_no,
            "grounding": round_grounding,
        }
        if best is None or _score(review) > _score(best["evaluation"]):
            best = candidate

        if _converged(review):
            best = candidate
            break

        new_objections = review.get("objections") or []
        # Anti-oscillation: if a later round doesn't reduce objections, stop and
        # keep the best draft rather than thrash (docs/BRD.md §8).
        if round_no >= 2 and len(new_objections) >= len(prior_objections):
            break
        prior_objections = new_objections
        # The round is done: carry only what the next round needs.
        st = {
            "round": round_no + 1,
            "prior_objections": prior_objections,
            "best": best,
        }

    assert best is not None
    # Context-only characters have no agent and so no retrieval; record them
    # anyway so the grounding view lists everyone in the beat (R5.3).
    context_only = [
        unsampled_entry(characters[n], CONTEXT_ONLY_REASON)
        for n in present[len(agents) :]
        if characters.get(n) is not None
    ]
    best["grounding"] = [*(best.get("grounding") or []), *context_only]
    best["rounds"] = round_no  # rounds actually run, not the best draft's round
    best["evaluation"]["converged"] = _converged(best["evaluation"])
    return best


def _score(review: dict) -> float:
    """A scalar for picking the best draft across rounds."""
    arc = float(review.get("arc_fit") or 0)
    cov = float(review.get("beat_coverage") or 0)
    penalty = 0.1 * len(_blocking(review.get("objections") or []))
    return max(0.0, (arc + cov) / 2 - penalty)


async def run_ensemble_scene_workflow(
    scene_id: UUID,
    scene_request: dict,
    user_id: UUID,
    *,
    book_id: Optional[UUID],
    chapter_summary: str,
    story_position: Optional[StoryPosition],
    prior_scene_tail: str = "",
    checkpoint: Optional[dict] = None,
    save_checkpoint=None,
    on_restart=None,
) -> dict:
    """Ensemble-mode workflow for a Scene the API layer already created.

    Mirrors run_prose_scene_workflow: plan beats, run the ensemble per beat,
    assemble, persist. Tier-gated at the caller; if ensemble isn't allowed this
    should never be reached. QuotaExhaustedError propagates so the worker pauses;
    the checkpoint (planner, then every agent call of every beat) makes the
    resume continue from the last completed call (SPEC-08).
    """
    from datetime import datetime, timezone

    from app.core.database import get_async_session
    from app.core.logging_config import log_error
    from app.core.orm_models import Scene
    from app.jobs.cancel import JobCancelled
    from app.llm.errors import QuotaExhaustedError
    from app.orchestration.prose import (
        CANON_CHANGED_NOTE,
        _canon_hash,
        plan_scene_beats,
        prose_tail,
        scene_setup,
        trim_seam_repeat,
    )

    started = datetime.now(timezone.utc)
    checkpoint = dict(checkpoint or {})
    try:
        if book_id is None:
            raise RuntimeError("scene generation needs its book (book is the root)")
        async with get_async_session() as session:
            setup = await scene_setup(session, book_id, scene_request, scene_id)
        canon_context = setup.canon.full_block()
        canon_hash = _canon_hash(
            setup, chapter_summary, story_position, prior_scene_tail
        )
        if checkpoint.get("canon_hash") not in (None, canon_hash):
            checkpoint = {}
            if on_restart is not None:
                await on_restart(CANON_CHANGED_NOTE)

        async def _save() -> None:
            if save_checkpoint is not None:
                await save_checkpoint(checkpoint)

        beats = checkpoint.get("beats")
        if beats is None:
            beats = await plan_scene_beats(
                scene_request,
                user_id,
                canon_block=canon_context,
                chapter_summary=chapter_summary,
                pov=setup.pov,
                story_position=story_position,
                # The planner sees the secondaries; the narrator and agent
                # prompts do not yet (W3-10).
                secondary_block=setup.secondary_block,
                plan=setup.plan,
                earlier=setup.earlier,
            )
            checkpoint = {
                "canon_hash": canon_hash,
                "beats": beats,
                "prose": [],
                "evaluations": [],
                "progress": {"stage": "beats", "done": 0, "total": len(beats)},
            }
            await _save()
        prose_parts: list[str] = list(checkpoint.get("prose") or [])
        evaluations: list[dict] = list(checkpoint.get("evaluations") or [])
        # {beat_index: [{character_id, chunk_id, score}]} — the prose workflow's
        # shape, so one grounding endpoint reads both modes (R5.3).
        grounding: dict[str, list] = dict(checkpoint.get("grounding") or {})
        prior_tail = prior_scene_tail
        if prose_parts:
            prior_tail = prose_tail("\n\n".join(prose_parts))

        for idx in range(len(prose_parts), len(beats)):
            beat = beats[idx]

            async def _save_beat(state: dict) -> None:
                checkpoint["beat_state"] = state
                await _save()

            result = await run_ensemble(
                scene_request={**scene_request, "characters": beat["characters"]},
                beat_description=beat["description"],
                user_id=user_id,
                book_id=book_id,
                canon_context=canon_context,
                scope=setup.scope,
                prior_tail=prior_tail,
                state=checkpoint.get("beat_state"),
                save_state=_save_beat,
                chapter_summary=chapter_summary,
            )
            prose_parts.append(trim_seam_repeat(result["prose"], prior_tail))
            evaluations.append(result["evaluation"])
            grounding[str(idx)] = result.get("grounding") or []
            prior_tail = prose_tail("\n\n".join(prose_parts))
            checkpoint.update(
                prose=prose_parts,
                evaluations=evaluations,
                grounding=grounding,
                beat_state=None,
                beat_index=idx,
                progress={
                    "stage": "beats",
                    "done": len(prose_parts),
                    "total": len(beats),
                },
            )
            await _save()

        scene_text = "\n\n".join(prose_parts)
        word_count = len(scene_text.split())
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        if word_count < 10:
            raise RuntimeError(
                f"ensemble produced no usable prose ({word_count} words)"
            )

        converged = all(e.get("converged") for e in evaluations)
        async with get_async_session() as session:
            scene = await session.get(Scene, scene_id)
            if scene is None:
                raise RuntimeError(f"Scene {scene_id} disappeared mid-workflow")
            scene.generated_content = scene_text
            scene.content = scene_text
            scene.word_count = word_count
            scene.status = "completed"
            scene.generation_time_ms = elapsed_ms
            # Scene.evaluation_scores is already a JSON metadata bucket — reuse it
            # (no migration) for the ensemble's per-beat reviews.
            scene.evaluation_scores = {
                "mode": "ensemble",
                "converged": converged,
                "beats": evaluations,
                "grounding": grounding,
            }
            await session.commit()

        return {
            "scene_id": str(scene_id),
            "status": "completed",
            "converged": converged,
        }

    except (QuotaExhaustedError, JobCancelled):
        raise  # pause (or stop for the author), don't fail
    except Exception as e:
        log_error(
            logger, e, context={"scene_id": str(scene_id), "event": "ensemble_failed"}
        )
        try:
            async with get_async_session() as session:
                scene = await session.get(Scene, scene_id)
                if scene is not None:
                    scene.status = "failed"
                    await session.commit()
        except Exception:
            pass
        return {"scene_id": str(scene_id), "status": "failed", "error": str(e)}
