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


async def _stage_beats(canon: Canon, chapters: list[dict], user_id) -> list[dict]:
    """S3: beats batched by act (~4 calls), reassembled in chapter order."""
    nodes: list[dict] = []
    for act_index in sorted(_by_act(chapters)):
        act_chapters = _by_act(chapters)[act_index]
        nodes.extend(
            await _stage_beats_for_act(canon, act_index, act_chapters, user_id)
        )
    return nodes


async def generate_staged_outline(
    canon: Canon,
    *,
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
    on_stage: StageCallback = None,
) -> tuple[list[dict], list[str]]:
    """Run S1-S4 and return (validated nodes, warnings).

    The S4 fidelity gate is deterministic: if a principal is missing, regenerate
    the beats ONCE, then save whatever we have with warnings attached — never
    hard-fail an expensive job on a heuristic (docs/BRD.md R1.6).
    """
    principals = canon.principals()
    known = canon.canon_terms()

    async def _emit(stage: str) -> None:
        if on_stage is not None:
            await on_stage(stage)

    await _emit("skeleton")
    skeleton = await _stage_skeleton(canon, chapters_target, user_id)

    await _emit("chapters")
    chapters = await _stage_chapters(canon, skeleton, user_id)

    await _emit("beats")
    nodes = await _stage_beats(canon, chapters, user_id)

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
        await _emit("chapters")
        chapters = await _stage_chapters(
            canon, skeleton, user_id, repair_missing=audit.missing
        )
        await _emit("beats")
        nodes = await _stage_beats(canon, chapters, user_id)
        audit = audit_outline(nodes, principals, known)

    return validate_outline_nodes(nodes), list(audit.warnings)
