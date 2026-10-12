"""Staged, canon-grounded outline generation (Phase 5, docs/BRD.md R5).

The single-call outline asks the model to do everything at once. Staging makes the
grounding explicit and cheap to check:

  S1 skeleton  — restate the premise, name the central conflict, block the acts.
                 `premise_restated` is the cheap trick that catches the "Elara"
                 incident at stage 1: forcing the model to say what the story IS
                 (and what "the CEL" is) surfaces a misread in ONE call, before
                 12 chapters are built on it.
  S2 chapters  — chapter titles/summaries with pov + which canon characters act.
  S3 beats     — scene beats per chapter, BATCHED BY ACT (~4 calls, not 12).
  S4 audit     — deterministic fidelity gate (app/planning/fidelity.py): if a
                 principal is missing, regenerate ONCE, then save with warnings.
                 An expensive job is never hard-failed on a heuristic.

The whole canon stays in context at every stage — we never truncate; that is
what the 1M window is for. Each stage's LLM call is its own function so the
orchestration (order, the audit->repair gate, stage progress) is testable with a
mocked client.

Checkpoints (SPEC-08): after the skeleton, after chapters, after each act's
beats and through the repair pass, so a run paused for quota resumes from its
last completed call. The audit is deterministic and needs none. A canon change
during the pause discards the checkpoint — stages generated against two canons
would be worse than paying twice.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional
from uuid import UUID

from app.core.llm_text import STORY_MATERIAL_NOTICE
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array, extract_json_object
from app.planning.canon import Canon
from app.planning.fidelity import FidelityAudit, audit_outline
from app.planning.outline import validate_outline_nodes

logger = setup_logging("planning.staged_outline")

StageCallback = Optional[Callable[[str], Awaitable[None]]]
CheckpointCallback = Optional[Callable[[dict], Awaitable[None]]]
NoteCallback = Optional[Callable[[str], Awaitable[None]]]


async def _stage_skeleton(canon: Canon, chapters_target: int, user_id) -> dict:
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon.full_block()}

You are structuring THIS book — the author's story, not a prompt to riff on.
First, ground yourself, then block out the acts.

Return ONLY a JSON object:
{{
  "premise_restated": "2-3 sentences restating what this story is ABOUT in your
     own words, naming the real protagonist(s), antagonist, and setting from the
     canon above. Resolve in-world terms and abbreviations to what they mean.",
  "central_conflict": "one sentence: the core conflict the book turns on",
  "acts": [
    {{"title": "act title", "goal": "what this act accomplishes",
      "turn": "the reversal/decision that ends it",
      "chapters_planned": <int, roughly {chapters_target} chapters across all acts>}}
  ]
}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=2048,
        user_id=user_id,
        purpose="outline:skeleton",
    )
    return extract_json_object(result.text)


async def _stage_chapters(
    canon: Canon, skeleton: dict, user_id, repair_missing: Optional[list[str]] = None
) -> list[dict]:
    cast_names = ", ".join(c.name for c in canon.characters) or "(none named)"
    repair = ""
    if repair_missing:
        # The chapter stage is where structural cast (pov, characters[]) is chosen,
        # so a recall failure is repaired HERE, not only at the beat stage
        # (PR review #5).
        repair = (
            "\n\nThe previous attempt OMITTED these principal characters entirely. "
            "You MUST give each of them a role across the chapters, present in the "
            f"`pov` or `characters` of specific chapters: {', '.join(repair_missing)}"
        )
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon.full_block()}

Premise: {skeleton.get("premise_restated", "")}
Central conflict: {skeleton.get("central_conflict", "")}
Acts: {skeleton.get("acts", [])}

Break the acts into chapters. Every name you use for a character MUST be one of
the canon cast: {cast_names}. Do not introduce new principal characters.{repair}

Return ONLY a JSON array of chapters:
[
  {{"title": "chapter title", "summary": "1-2 sentences of what CHANGES",
    "act_index": <int, 0-based>, "pov": "canon character name or ''",
    "characters": ["canon names present"], "threads": ["optional plot threads"]}}
]

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=4096,
        user_id=user_id,
        purpose="outline:chapters",
    )
    return extract_json_array(result.text)


async def _stage_beats_for_act(
    canon: Canon, act_index: int, act_chapters: list[dict], user_id
) -> list[dict]:
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon.full_block()}

Here are the chapters of act {act_index}, in order:
{act_chapters}

For EACH chapter, write 2-4 scene beats. Keep every character name faithful to
the canon. Each beat's summary must say what CHANGES, not just what happens.

Return ONLY a JSON array mirroring the chapters, each with its beats as children:
[
  {{"title": "<chapter title>", "summary": "<chapter summary>",
    "children": [{{"title": "beat title", "summary": "what changes", "children": []}}]}}
]

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        user_id=user_id,
        purpose="outline:beats",
    )
    return extract_json_array(result.text)


def _by_act(chapters: list[dict]) -> dict[int, list[dict]]:
    acts: dict[int, list[dict]] = {}
    for ch in chapters:
        idx = ch.get("act_index", 0)
        if not isinstance(idx, int):
            idx = 0
        acts.setdefault(idx, []).append(ch)
    return acts


async def _stage_beats(
    canon: Canon,
    chapters: list[dict],
    user_id,
    done: Optional[dict] = None,
    on_act: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> list[dict]:
    """S3: beats batched by act (~4 calls), reassembled in chapter order.

    `done` maps str(act_index) -> that act's nodes from a checkpoint; those
    acts are not called again. `on_act(done)` runs after each new act.
    """
    done = dict(done or {})
    by_act = _by_act(chapters)
    for act_index in sorted(by_act):
        if str(act_index) in done:
            continue
        done[str(act_index)] = await _stage_beats_for_act(
            canon, act_index, by_act[act_index], user_id
        )
        if on_act is not None:
            await on_act(done)
    nodes: list[dict] = []
    for act_index in sorted(by_act):
        nodes.extend(done[str(act_index)])
    return nodes


def canon_hash(canon: Canon) -> str:
    import hashlib

    return hashlib.sha256(canon.full_block().encode("utf-8")).hexdigest()[:16]


async def generate_staged_outline(
    canon: Canon,
    *,
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
    on_stage: StageCallback = None,
    checkpoint: Optional[dict] = None,
    save_checkpoint: CheckpointCallback = None,
    on_restart: NoteCallback = None,
) -> tuple[list[dict], list[str]]:
    """Run S1-S4 and return (validated nodes, warnings).

    The S4 fidelity gate is deterministic: if a principal is missing, regenerate
    the beats ONCE, then save whatever we have with warnings attached — never
    hard-fail an expensive job on a heuristic (docs/BRD.md R1.6).

    Checkpoint shape: {canon_hash, skeleton, passes: [{chapters, acts}],
    progress}. Pass 0 is the first draft, pass 1 the repair; both are replayed
    deterministically from what was saved.
    """
    principals = canon.principals()
    known = canon.canon_terms()
    chash = canon_hash(canon)
    ck = dict(checkpoint or {})
    if ck.get("canon_hash") not in (None, chash):
        from app.orchestration.prose import CANON_CHANGED_NOTE

        logger.info(
            "Canon changed during pause; restarting outline from scratch",
            extra_fields={"event": "outline_checkpoint_discarded"},
        )
        ck = {}
        if on_restart is not None:
            await on_restart(CANON_CHANGED_NOTE)
    ck["canon_hash"] = chash
    passes: list[dict] = [dict(p) for p in ck.get("passes") or []]
    ck["passes"] = passes

    async def _emit(stage: str) -> None:
        if on_stage is not None:
            await on_stage(stage)

    async def _save(stage: str) -> None:
        # Progress counts model calls: skeleton + per pass (chapters + acts).
        done = (1 if "skeleton" in ck else 0) + sum(
            (1 if "chapters" in p else 0) + len(p.get("acts") or {}) for p in passes
        )
        total = 1 + sum(1 + len(_by_act(p.get("chapters") or [])) for p in passes)
        ck["progress"] = {"stage": stage, "done": done, "total": max(total, done)}
        if save_checkpoint is not None:
            await save_checkpoint(ck)

    async def _run_pass(index: int, repair_missing: Optional[list[str]]) -> list:
        if len(passes) <= index:
            passes.append({})
        p = passes[index]
        if "chapters" not in p:
            await _emit("chapters")
            p["chapters"] = await _stage_chapters(
                canon, ck["skeleton"], user_id, repair_missing=repair_missing
            )
            p["acts"] = {}
            await _save("chapters")

        async def _on_act(done: dict) -> None:
            p["acts"] = done
            await _save("beats")

        await _emit("beats")
        return await _stage_beats(
            canon, p["chapters"], user_id, done=p.get("acts"), on_act=_on_act
        )

    if "skeleton" not in ck:
        await _emit("skeleton")
        ck["skeleton"] = await _stage_skeleton(canon, chapters_target, user_id)
        await _save("skeleton")

    nodes = await _run_pass(0, None)

    await _emit("audit")
    audit: FidelityAudit = audit_outline(nodes, principals, known)
    if not audit.passed:
        logger.warning(
            "Outline failed the principal-cast gate; regenerating once",
            extra_fields={
                "event": "outline_fidelity_repair",
                "missing": audit.missing,
                "recall": audit.principal_recall,
            },
        )
        # Repair at the CHAPTER stage (where cast is chosen) with the missing
        # principals as explicit feedback, then re-derive beats (PR review #5).
        nodes = await _run_pass(1, audit.missing)
        audit = audit_outline(nodes, principals, known)

    return validate_outline_nodes(nodes), list(audit.warnings)
