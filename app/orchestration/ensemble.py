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

from app.characters.context import build_character_context, load_characters_for_book
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_object
from app.llm.tier import get_tier

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
    character, name: str, beat_description: str, prior_objections: list[dict], user_id
) -> dict:
    """One character agent's proposal — scoped to its OWN context only."""
    context = await build_character_context(character, name, beat_description)
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

{as_quoted_block(context, "character")}

The moment: {clean_for_llm(beat_description)}{feedback}

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

Setting: {clean_for_llm(str(scene_request.get("setting", "")))}
Prior scene tail: {clean_for_llm(prior_tail)[:600]}

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
    draft: str, canon_context: str, beat_description: str, present: list[str], user_id
) -> dict:
    """Story editor: measured review — arc fit, coverage, who actually acts."""
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_context}

The beat to cover: {clean_for_llm(beat_description)}
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
    prior_tail: str = "",
) -> dict:
    """Run the ensemble loop for one beat. Returns {prose, evaluation, rounds}.

    Tier-gated: rounds and agent count come from the Tier; free tier is 1 round,
    <=3 proposal agents. Characters over that cap are context-only — they still
    appear in narration AND editor validation, never silently dropped (PR review
    #6). Convergence is measured; non-convergence keeps the best draft with
    objections (never a hard fail).
    """
    tier = get_tier()
    present = list(dict.fromkeys(scene_request.get("characters") or []))  # dedup, order
    agents = present[: tier.max_agents]  # only these get their own proposal agent

    characters = await load_characters_for_book(
        agents, user_id=user_id, book_id=book_id
    )

    prior_objections: list[dict] = []
    best: Optional[dict] = None

    for round_no in range(1, tier.max_rounds + 1):
        proposals = [
            await propose(
                characters.get(name), name, beat_description, prior_objections, user_id
            )
            for name in agents
        ]
        draft = await narrate(
            proposals,
            scene_request,
            canon_context,
            prior_tail,
            user_id,
            present=present,
        )
        # Editor validates the FULL present cast, so a context-only character who
        # vanished is caught as a blocking objection, not reported as converged.
        review = await editor_review(
            draft, canon_context, beat_description, present, user_id
        )
        candidate = {"prose": draft, "evaluation": review, "rounds": round_no}
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

    assert best is not None
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
    book_id: Optional[UUID] = None,
    chapter_summary: str = "",
    prior_scene_tail: str = "",
) -> dict:
    """Ensemble-mode workflow for a Scene the API layer already created.

    Mirrors run_prose_scene_workflow: plan beats, run the ensemble per beat,
    assemble, persist. Tier-gated at the caller; if ensemble isn't allowed this
    should never be reached. QuotaExhaustedError propagates so the worker pauses.
    """
    from datetime import datetime, timezone

    from app.core.database import get_async_session
    from app.core.logging_config import log_error
    from app.core.orm_models import Scene
    from app.llm.errors import QuotaExhaustedError
    from app.orchestration.prose import PREV_TAIL_WORDS, plan_scene_beats
    from app.planning.canon import build_canon

    started = datetime.now(timezone.utc)
    try:
        if book_id is not None:
            async with get_async_session() as session:
                canon = await build_canon(session, book_id)
            canon_context = canon.full_block()
        else:
            canon_context = ""

        beats = await plan_scene_beats(scene_request, user_id)
        prose_parts: list[str] = []
        evaluations: list[dict] = []
        prior_tail = prior_scene_tail

        for beat in beats:
            result = await run_ensemble(
                scene_request={**scene_request, "characters": beat["characters"]},
                beat_description=beat["description"],
                user_id=user_id,
                book_id=book_id,
                canon_context=canon_context,
                prior_tail=prior_tail,
            )
            prose_parts.append(result["prose"])
            evaluations.append(result["evaluation"])
            joined = "\n\n".join(prose_parts)
            prior_tail = " ".join(joined.split()[-PREV_TAIL_WORDS:])

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
            }
            await session.commit()

        return {
            "scene_id": str(scene_id),
            "status": "completed",
            "converged": converged,
        }

    except QuotaExhaustedError:
        raise  # pause, don't fail
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
