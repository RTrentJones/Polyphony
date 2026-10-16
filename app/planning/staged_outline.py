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

Premise checkpoint (SPEC-10, BRD R1.3): with `confirm_premise` the job runs
S1 only (`generate_premise`) and stops; the plan shows the restated premise to
the author, and confirming it enqueues a continuation that passes the
(possibly edited) skeleton back in as `skeleton=` — authoritative, so S1 is
never re-run or second-guessed by a canon-drift restart.

Checkpoints (SPEC-08): after the skeleton, after chapters, after each act's
beats and through the repair pass, so a run paused for quota resumes from its
last completed call. The audit is deterministic and needs none. A canon change
during the pause discards the checkpoint — stages generated against two canons
would be worse than paying twice.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional
from uuid import UUID

from app.core.llm_text import STORY_MATERIAL_NOTICE
from app.core.logging_config import setup_logging
from app.core.prompt_markers import (
    ACT_CHAPTERS_HEADER,
    CONFIRMED_PREMISE_NOTE,
    EARLIER_BEATS,
    OUTLINE_CHAPTERS_HEADER,
)
from app.llm.client import get_llm_client
from app.llm.json_utils import JSON_OBJECT, extract_json_array, extract_json_object
from app.planning.canon import Canon
from app.parsing import names
from app.planning.fidelity import FidelityAudit, audit_outline, canon_known_text
from app.planning.outline import CastIndex, validate_outline_nodes
from app.planning.planned_beat import marked_plan

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
        response_format=JSON_OBJECT,
    )
    return extract_json_object(result.text)


def _cast_rules(canon: Canon) -> str:
    """Who the outline may name (SPEC-24 §1e). Principals are the canon cast
    and only the canon cast; people the synopsis or canon name beyond it may
    appear by name (the author wrote them); anyone else is an invention. The
    model lists everyone in one `people` list and the server classifies it
    (outline.classify_people), so the prompt never guesses who is a person."""
    cast_names = ", ".join(c.name for c in canon.characters) or "(none named)"
    return (
        f"Principal characters MUST be canon cast: {cast_names}. "
        "Do not introduce new principal characters.\n"
        "People the synopsis or canon name who are not in the cast may appear "
        "by name; never invent others.\n"
        "`pov` names one canon cast member; `people` lists everyone named in "
        "that chapter or beat: canon cast, or people the synopsis or canon name."
    )


def _json(value) -> str:
    """Structures embedded in a prompt are JSON — the format the model is asked
    to answer in — never a Python repr (`[{'title': ...}]`) (SPEC-18, B11)."""
    return json.dumps(value, ensure_ascii=False, indent=1)


def _premise_lines(skeleton: dict) -> str:
    """The premise and central conflict, with the author's confirmation said
    out loud: a premise the author confirmed (or rewrote) is the ground truth
    for the rest of the outline (SPEC-10), at every stage that follows."""
    confirmed = CONFIRMED_PREMISE_NOTE if skeleton.get("confirmed") else ""
    return (
        f"Premise{confirmed}: {skeleton.get('premise_restated', '')}\n"
        f"Central conflict: {skeleton.get('central_conflict', '')}"
    )


def _acts_for_prompt(skeleton: dict) -> list:
    """The skeleton's acts, each labelled with its 1-based number and the
    0-based `act_index` the chapters must carry."""
    acts = skeleton.get("acts") or []
    if not isinstance(acts, list):
        return []
    return [
        {"act": i + 1, "act_index": i, **(a if isinstance(a, dict) else {"title": a})}
        for i, a in enumerate(acts)
    ]


def _act_brief(skeleton: Optional[dict], act_index: int) -> str:
    """This act's title, goal and turn, numbered from 1 (SPEC-18, B11)."""
    acts = (skeleton or {}).get("acts") or []
    number = act_index + 1
    if not isinstance(acts, list) or not 0 <= act_index < len(acts):
        return f"This is act {number}."
    act = acts[act_index] if isinstance(acts[act_index], dict) else {}
    lines = [f"This is act {number} of {len(acts)}: {act.get('title', '')}".rstrip()]
    if act.get("goal"):
        lines.append(f"Act {number} goal: {act['goal']}")
    if act.get("turn"):
        lines.append(
            f"Act {number} turn (the reversal or decision that ends it — the "
            f"final chapter's beats must land it): {act['turn']}"
        )
    return "\n".join(lines)


async def _stage_chapters(
    canon: Canon, skeleton: dict, user_id, repair_missing: Optional[list[str]] = None
) -> list[dict]:
    repair = ""
    if repair_missing:
        # The chapter stage is where structural cast (pov, characters[]) is chosen,
        # so a recall failure is repaired HERE, not only at the beat stage
        # (PR review #5).
        repair = (
            "\n\nThe previous attempt OMITTED these principal characters entirely. "
            "You MUST give each of them a role across the chapters, present in the "
            f"`pov` or `people` of specific chapters: {', '.join(repair_missing)}"
        )
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon.full_block()}

{_premise_lines(skeleton)}
Acts, numbered from 1 (each chapter's act_index is its act's 0-based index):
{_json(_acts_for_prompt(skeleton))}

Break the acts into chapters.
{_cast_rules(canon)}{repair}

Return ONLY a JSON object whose "items" are the chapters:
{{"items": [
  {{"title": "chapter title", "summary": "1-2 sentences of what CHANGES",
    "act_index": <int, 0-based>, "pov": "canon character name or ''",
    "people": ["everyone named in this chapter"],
    "threads": ["optional plot threads"]}}
]}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=4096,
        user_id=user_id,
        purpose="outline:chapters",
        response_format=JSON_OBJECT,
    )
    return extract_json_array(result.text)


INTRODUCE_NO_EARLIER = (
    "Introduce each person no earlier than the chapter that introduces them."
)


def outline_context(
    outline: list[dict], act_chapters: list[dict], earlier: list[dict]
) -> str:
    """The whole book for one act's beats (SPEC-38): every chapter in order,
    this act's marked (their summaries are in the act's JSON below), and the
    beat titles earlier acts already have, so an act cannot bring a person in
    before the chapter that introduces them (W4-5)."""
    # Model output: a node or a beat that is not an object is skipped.
    done = []
    for ch in (c for c in earlier if isinstance(c, dict)):
        kids = [b for b in ch.get("children") or [] if isinstance(b, dict)]
        titles = "; ".join(str(b.get("title") or "") for b in kids)
        done.append(f"- {ch.get('title', '')}: {titles}")
    mine = {i for i, ch in enumerate(outline) if ch in act_chapters}
    items = [
        ch.get("title", "") + ("" if i in mine else f": {ch.get('summary', '')}")
        for i, ch in enumerate(outline)
    ]
    earlier_header = EARLIER_BEATS.format(what="earlier acts")
    lines = marked_plan(OUTLINE_CHAPTERS_HEADER, items, mine, earlier_header, done)
    return "\n".join([*lines, INTRODUCE_NO_EARLIER])


async def _stage_beats_for_act(
    canon: Canon,
    act_index: int,
    user_id,
    *,
    skeleton: Optional[dict] = None,
    outline: list[dict],
    earlier: list[dict],
) -> list[dict]:
    """S3 for one act. The beats see the plan they are detailing: the premise
    (authoritative when the author confirmed it), the central conflict, this
    act's goal and turn, and the whole book around it (`outline_context`) —
    without them beats drift from the agreed story (SPEC-18, B11, SPEC-38).
    Acts are numbered from 1 in the prompt; the stored `act_index` stays
    0-based."""
    number = act_index + 1
    act_chapters = _by_act(outline).get(act_index, [])
    plan = f"{_premise_lines(skeleton)}\n\n" if skeleton else ""
    # Title placeholders below are deliberately unquoted: the template is not
    # an example the model (or the offline stub) should mistake for a chapter.
    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon.full_block()}

{plan}{_act_brief(skeleton, act_index)}

{outline_context(outline, act_chapters, earlier)}

{ACT_CHAPTERS_HEADER.format(number=number)}
{_json(act_chapters)}

For EACH chapter, write 2-4 scene beats. Keep every character name faithful to
the canon.
{_cast_rules(canon)}

Each beat's summary must say what CHANGES, not just what happens. The beats
must serve the premise and this act's goal, and the act's final chapter must
end on its turn. Give each beat its own `people`: everyone named in THAT beat
(not the whole chapter's), plus `setting` where it happens and `tone` its
emotional tone. A functional figure the canon does not name (a clerk, a guard)
stays unnamed.
Write each scene summary as what the chapter's POV character witnesses; never what they fail to notice or will later learn.
Do not reuse a chapter title as a scene title.

Return ONLY a JSON object, its "items" mirroring the chapters, each with its
beats as children. Echo each chapter's title, summary, act_index, pov, people
and threads unchanged:
{{"items": [
  {{"title": <chapter title>, "summary": <chapter summary>,
    "act_index": <int>, "pov": <pov>, "people": [<names>],
    "threads": [<threads>],
    "children": [{{"title": <beat title>, "summary": <what changes>,
      "people": [<everyone named in this beat>],
      "setting": <where it happens>, "tone": <its emotional tone>,
      "children": []}}]}}
]}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        user_id=user_id,
        purpose="outline:beats",
        response_format=JSON_OBJECT,
    )
    return extract_json_array(result.text)


_STRUCTURAL = ("act_index", "pov", "people", "threads")
_EMPTY = (None, "", [])


def merge_structural(chapters: list[dict], nodes: list[dict]) -> list[dict]:
    """Carry S2's structural fields onto S3's nodes by position.

    The beat stage is asked to echo them, but a model may not; S2 is where the
    cast was chosen, so it is the fallback for any field a node lacks."""
    merged = []
    for i, node in enumerate(nodes):
        source = chapters[i] if i < len(chapters) else None
        if isinstance(node, dict) and isinstance(source, dict):
            node = dict(node)
            for key in _STRUCTURAL:
                if node.get(key) in _EMPTY and source.get(key) not in _EMPTY:
                    node[key] = source[key]
        merged.append(_with_scene_people(node))
    return merged


def _with_scene_people(node):
    """W6-4 (c): a chapter's `people` also lists everyone its scenes name."""
    if not isinstance(node, dict) or not isinstance(node.get("people"), list):
        return node
    people = list(node["people"])
    for scene in node.get("children") or []:
        named = scene.get("people") if isinstance(scene, dict) else None
        if isinstance(named, list):
            people += [p for p in named if p not in people]
    return {**node, "people": people}


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
    *,
    skeleton: Optional[dict] = None,
) -> list[dict]:
    """S3: beats batched by act (~4 calls), reassembled in chapter order.

    `done` maps str(act_index) -> that act's nodes from a checkpoint; those
    acts are not called again. `on_act(done)` runs after each new act.
    """
    done = dict(done or {})
    by_act = _by_act(chapters)
    # Nodes come back in act order, so the book is read the same way.
    ordered = [ch for act_index in sorted(by_act) for ch in by_act[act_index]]
    for act_index in sorted(by_act):
        if str(act_index) in done:
            continue
        done[str(act_index)] = await _stage_beats_for_act(
            canon,
            act_index,
            user_id,
            skeleton=skeleton,
            outline=ordered,
            earlier=[
                n for a in sorted(done, key=int) if int(a) < act_index for n in done[a]
            ],
        )
        if on_act is not None:
            await on_act(done)
    nodes: list[dict] = []
    for act_index in sorted(by_act):
        nodes.extend(done[str(act_index)])
    return merge_structural(ordered, nodes)


def canon_hash(canon: Canon) -> str:
    import hashlib

    return hashlib.sha256(canon.full_block().encode("utf-8")).hexdigest()[:16]


def _cast_index(canon: Canon) -> Optional[CastIndex]:
    index = CastIndex(canon.characters)
    return index if index.names else None


async def generate_premise(
    canon: Canon,
    *,
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
    on_stage: StageCallback = None,
    checkpoint: Optional[dict] = None,
    save_checkpoint: CheckpointCallback = None,
) -> dict:
    """S1 alone, for the premise checkpoint (SPEC-10): one call, then stop.

    A checkpointed skeleton for the same canon is reused, so a run that paused
    before it could record the result does not pay twice."""
    chash = canon_hash(canon)
    ck = checkpoint or {}
    if ck.get("canon_hash") == chash and isinstance(ck.get("skeleton"), dict):
        return ck["skeleton"]
    if on_stage is not None:
        await on_stage("skeleton")
    skeleton = await _stage_skeleton(canon, chapters_target, user_id)
    if save_checkpoint is not None:
        await save_checkpoint(
            {
                "canon_hash": chash,
                "skeleton": skeleton,
                "passes": [],
                "progress": {"stage": "skeleton", "done": 1, "total": 1},
            }
        )
    return skeleton


async def generate_staged_outline(
    canon: Canon,
    *,
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
    on_stage: StageCallback = None,
    checkpoint: Optional[dict] = None,
    save_checkpoint: CheckpointCallback = None,
    on_restart: NoteCallback = None,
    skeleton: Optional[dict] = None,
    on_premise: CheckpointCallback = None,
) -> tuple[list[dict], list[str]]:
    """Run S1-S4 and return (validated nodes, warnings).

    The S4 fidelity gate is deterministic: if a principal is missing, regenerate
    the beats ONCE, then save whatever we have with warnings attached — never
    hard-fail an expensive job on a heuristic (docs/BRD.md R1.6).

    Checkpoint shape: {canon_hash, skeleton, passes: [{chapters, acts}],
    progress}. Pass 0 is the first draft, pass 1 the repair; both are replayed
    deterministically from what was saved.

    `skeleton` is an author-confirmed S1 result (SPEC-10): it replaces the
    stage and survives a canon-drift restart. `on_premise(skeleton)` runs once
    S1 is known, so the plan can show the premise while later stages run.
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
    if skeleton is not None:
        ck["skeleton"] = skeleton
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
            canon,
            p["chapters"],
            user_id,
            done=p.get("acts"),
            on_act=_on_act,
            skeleton=ck["skeleton"],
        )

    if "skeleton" not in ck:
        await _emit("skeleton")
        ck["skeleton"] = await _stage_skeleton(canon, chapters_target, user_id)
        await _save("skeleton")
    if on_premise is not None:
        await on_premise(ck["skeleton"])

    nodes = await _run_pass(0, None)

    await _emit("audit")
    cast = _cast_index(canon)
    canon_text = canon_known_text(canon)
    known_text = names.norm(canon_text)
    audit: FidelityAudit = audit_outline(
        nodes, principals, known, cast=cast, canon_text=canon_text
    )
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
        audit = audit_outline(
            nodes, principals, known, cast=cast, canon_text=canon_text
        )

    return (
        validate_outline_nodes(nodes, cast=cast, known_text=known_text),
        list(audit.warnings),
    )
