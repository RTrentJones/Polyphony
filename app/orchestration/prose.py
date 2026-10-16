"""Prose-mode scene generation: one LLM call per beat.

Prose mode assembles every character's voice context into a single beat prompt
and writes the beat's full prose in one call: 1 planning call + 3-5 beat calls
per scene. It is the default mode and the only one the free tier allows.

Every call carries the book's whole Canon — synopsis, cast, canon entries and
style guide — fenced as story material (SPEC-01; docs/BRD.md R1.1, R2.3). This
path used to see only the request form, so a book whose style guide said "first
person, present tense" was written however the form happened to say, and a
scene set in Aeon Holdings knew nothing about Aeon Holdings.
"""

import re
from datetime import datetime, timezone
from typing import NamedTuple, Optional
from uuid import UUID

from sqlalchemy import func, select

from app.characters.context import SceneScope, build_cast_context
from app.core.database import get_async_session
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Chapter, Scene, SceneBeat, Source
from app.core.prompt_markers import (
    BEAT_TO_WRITE_LABEL,
    BEATS_FENCE,
    CHARACTERS_SECTION,
    CURRENT_BEAT_MARK,
    PRIOR_FENCE,
    STOP_AT_NEXT_BEAT,
)
from app.llm.client import get_llm_client
from app.llm.errors import QuotaExhaustedError
from app.planning.canon import Canon, build_canon
from app.planning.fidelity import canon_known_text, unknown_proper_nouns

from .pov import NAMING_RULE, Pov, pov_instruction, resolve_pov
from .workflow import (
    StoryPosition,
    chapter_context,
    plan_scene_beats,
    scene_block,
    secondary_names,
    story_position_line,
)

logger = setup_logging("orchestration.prose")

# Tail of the previous scene fed into the next one for continuity
PREV_TAIL_WORDS = 500

# House style, shown ONLY when the book has neither a style-guide tone nor
# sample prose (SPEC-18). A dialogue-driven book's own guide must win.
GENERIC_CRAFT_GUIDANCE = (
    "Ground the reader in the setting and in the characters' actions, gestures, "
    "expressions, and interiority — description and action carry the scene; "
    "dialogue PUNCTUATES it, it does not dominate."
)

# A sentence ends at . ! ? or … (optionally followed by closing quotes or
# brackets) and whitespace, before a capital or an opening quote.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])[\"”’'\)\]]*\s+(?=[\"“‘'(\[]?[A-Z0-9])")
# "Mr. Voss" is not a sentence end.
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "prof", "sr", "jr", "mt", "vs"}
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")

# Recorded on the job when a resume discards its checkpoint (SPEC-08 §4).
CANON_CHANGED_NOTE = (
    "The canon changed while this job was paused (or the scene before it was "
    "edited), so it restarted from scratch rather than mix work generated "
    "against two versions of the book."
)


def scene_cast(canon, scene_request: dict) -> list:
    """The scene's cast as Character rows from the book's Canon.

    Resolved by ID (the API validated them against the book); legacy payloads
    that carry only names resolve by exact name. A row that is not in this book's
    Canon is never invented."""
    ids = {str(i) for i in scene_request.get("character_ids") or []}
    by_id = {str(c.id): c for c in canon.characters}
    if ids:
        return [by_id[i] for i in scene_request.get("character_ids") if i in by_id]
    names = scene_request.get("characters") or []
    by_name = {c.name: c for c in canon.characters}
    return [by_name[n] for n in names if n in by_name]


class SceneSetup(NamedTuple):
    """What a scene is written against (SPEC-29/30, SPEC-40/43)."""

    canon: Canon
    cast: list  # Character rows, from scene_cast
    scope: SceneScope
    pov: Pov
    secondary_block: str  # render_secondaries; "" without Also-named people
    plan: list  # book_plan: (Chapter.number, planned beat) for the whole book
    earlier: list  # earlier_beats: (title, beat lines) of the scenes before it
    chapter_summary: str  # scene_chapter: "" without a chapter
    position: Optional[StoryPosition]  # scene_chapter: None without a live chapter


async def scene_setup(
    session, book_id: UUID, scene_request: dict, scene_id: UUID
) -> SceneSetup:
    """The one scene setup for prose, ensemble and revise: the book's canon, the
    request's cast from it, the scene scope with its Also-named secondaries,
    the POV, the rendered secondary block, the book's plan and what the
    chapter's earlier scenes already planned."""
    from app.characters.context import later_plot_from
    from app.parsing.character_extractor import source_paragraphs
    from app.planning.planned_beat import beat_scene_description
    from app.planning.secondaries import render_secondaries, scene_secondaries

    canon = await build_canon(session, book_id)
    names = secondary_names(scene_request)
    cast = scene_cast(canon, scene_request)
    pov = resolve_pov(scene_request, canon)
    plan = await book_plan(session, book_id)
    scene = await session.get(Scene, scene_id)
    earlier = await earlier_beats(session, scene)
    # SPEC-40: every plan line a movement sample may not restate, and where each
    # source's later plot begins (nowhere without a story position).
    plot = [scene_request.get("scene_description") or ""]
    plot += [beat_scene_description(b) for _, b in plan]
    plot += [line for _, lines in earlier for line in lines]
    rows = await session.execute(
        select(Source.id, Source.content_text)
        .where(Source.book_id == book_id, Source.deleted_at.is_(None))
        .order_by(Source.uploaded_at)
    )
    sources = [(str(sid), text) for sid, text in rows if text]
    summary, position = await scene_chapter(session, scene) if scene else ("", None)
    later_from = {}
    for sid, text in sources if position else []:
        cut = later_plot_from(source_paragraphs(text), position.chapter, plan)
        if cut is not None:
            later_from[sid] = cut
    # The brief stays at plot[0], even empty (the scripted rule reads it there).
    plot = dict.fromkeys([plot[0], *filter(None, plot[1:])])
    scope = SceneScope.of(canon, cast, names, plot, later_from)
    block = render_secondaries(scene_secondaries(names, canon, sources, later_from))
    return SceneSetup(canon, cast, scope, pov, block, plan, earlier, summary, position)


def scene_name_warnings(prose, canon, scene_request, earlier) -> list[str]:
    """Possible inventions a generated scene names (SPEC-48; warn-only)."""
    brief = scene_request.get("scene_description") or ""  # the author's names
    return unknown_proper_nouns(
        prose,
        {*canon.canon_terms(), *secondary_names(scene_request)},
        canon_text="\n".join((canon_known_text(canon), brief, earlier)),
    )


# A last-beat instruction that keeps the close inside the scene (SPEC-24, W2-4):
# "end the scene here" invited an omniscient wrap-up paragraph.
LAST_BEAT_ENDING = "end the scene inside the moment, in POV; no summary of what follows"


async def scene_chapter(session, scene) -> tuple[str, Optional[StoryPosition]]:
    """(summary, position) of the scene's chapter for the scene jobs and revise
    (SPEC-30); ("", None) without one. Only a live chapter has a position: a
    trashed one keeps a stale `position` (review R-3)."""
    chapter = await session.get(Chapter, scene.chapter_id) if scene.chapter_id else None
    if chapter is None:
        return "", None
    if chapter.deleted_at is not None:
        return chapter.summary or "", None
    live = await session.scalar(
        select(func.count(Chapter.id)).where(
            Chapter.book_id == chapter.book_id, Chapter.deleted_at.is_(None)
        )
    )
    return chapter.summary or "", StoryPosition(chapter.number, live)


async def scene_prior_tail(session, scene) -> str:
    """The tail of the chapter's previous completed scene (SPEC-18), or ""."""
    from app.exports.builder import scene_text

    if scene.chapter_id is None:
        return ""
    prev = await session.scalar(
        select(Scene)
        .where(
            Scene.chapter_id == scene.chapter_id,
            Scene.status == "completed",
            Scene.position < scene.position,
            Scene.deleted_at.is_(None),  # trashed prose is not context
        )
        .order_by(Scene.position.desc())
        .limit(1)
    )
    return prose_tail(scene_text(prev)) if prev is not None else ""


async def book_plan(session, book_id: UUID) -> list[tuple[int, dict]]:
    """(Chapter.number, planned beat) for every live chapter's planned beats,
    by chapter position then beat order: the ONE reading of the book's plan
    (SPEC-40, SPEC-43). A beat's line is `beat_scene_description(beat)`."""
    from app.planning.planned_beat import normalize_planned_beats

    chapters = await session.scalars(
        select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.deleted_at.is_(None))
        .order_by(Chapter.position)
    )
    return [
        (chapter.number, beat)
        for chapter in chapters
        for beat in normalize_planned_beats(chapter.planned_beats)
    ]


async def earlier_beats(session, scene) -> list[tuple[str, list[str]]]:
    """(title, beat lines) of the chapter's live completed scenes before
    `scene`, by position then beat_number (SPEC-43); [] without a chapter."""
    from app.jobs.scene_batch import scene_title

    if scene is None or scene.chapter_id is None:
        return []
    earlier = (
        await session.scalars(
            select(Scene)
            .where(
                Scene.chapter_id == scene.chapter_id,
                Scene.status == "completed",
                Scene.position < scene.position,
                Scene.deleted_at.is_(None),
            )
            .order_by(Scene.position)
        )
    ).all()
    beats = (
        await session.scalars(
            select(SceneBeat)
            .where(SceneBeat.scene_id.in_([s.id for s in earlier]))
            .order_by(SceneBeat.beat_number)
        )
    ).all()
    return [
        (
            s.title or scene_title(s.scene_request or {}),
            [b.description or "" for b in beats if b.scene_id == s.id],
        )
        for s in earlier
    ]


def _sentence_starts(paragraph: str) -> list[int]:
    """Offsets where a sentence begins in `paragraph` (0 is always one)."""
    starts = [0]
    for m in _SENTENCE_BREAK.finditer(paragraph):
        before = paragraph[: m.start()].rstrip("\"”’')]")
        word = before.rsplit(None, 1)[-1] if before.split() else ""
        if word.rstrip(".").lower() in _ABBREVIATIONS:
            continue
        starts.append(m.end())
    return starts


def _paragraphs(text: str) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]


def prose_tail(text: str, words: int = PREV_TAIL_WORDS) -> str:
    """The end of `text`, at least `words` words long, cut at a seam a reader
    would recognise (SPEC-18).

    Whole paragraphs are taken from the end until the budget is met, and
    paragraph breaks are kept: the old `" ".join(split()[-N:])` tail flattened
    the prior prose into one wall of text, which the model then imitated, and
    usually opened it mid-sentence. When the earliest kept paragraph is longer
    than the budget still needed, it starts at the latest sentence boundary
    that still meets the budget — never mid-sentence. The tail is a suffix of
    the text (modulo whitespace), so its last `words` words are exactly the old
    tail's — which keeps old checkpoint fingerprints valid (`_canon_hash`).
    """
    paragraphs = _paragraphs(text)
    kept: list[str] = []
    count = 0
    for paragraph in reversed(paragraphs):
        kept.insert(0, paragraph)
        count += len(paragraph.split())
        if count >= words:
            break
    if kept and count > words:
        first = kept[0]
        needed = words - (count - len(first.split()))
        for start in reversed(_sentence_starts(first)):
            if len(first[start:].split()) >= needed:
                kept[0] = first[start:]
                break
    return "\n\n".join(kept)


def _sentences(text: str) -> list[str]:
    starts = _sentence_starts(text) + [len(text)]
    return [text[a:b].strip() for a, b in zip(starts, starts[1:]) if text[a:b].strip()]


def _norm_sentence(sentence: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", sentence.lower()).split())


def trim_seam_repeat(prose: str, prior: str) -> str:
    """Drop a beat's opening sentence when it restates the last sentence of
    the prose before it (SPEC-18 seam control).

    Deterministic and conservative: only an exact repeat (ignoring case,
    punctuation and spacing) of a sentence of at least four words, and only at
    the seam. Anything subtler is the prompt's job ("Do not restate anything in
    <prior>"); a trim that guessed would destroy prose the model meant.
    """
    prior_paragraphs = _paragraphs(prior)
    body = (prose or "").lstrip()
    if not prior_paragraphs or not body:
        return prose
    prior_sentences = _sentences(prior_paragraphs[-1])
    first_paragraph = _paragraphs(body)[0]
    beat_sentences = _sentences(first_paragraph)
    if not prior_sentences or not beat_sentences:
        return prose
    last = _norm_sentence(prior_sentences[-1])
    opening = beat_sentences[0]
    if len(last.split()) < 4 or _norm_sentence(opening) != last:
        return prose
    rest = body[body.index(opening) + len(opening) :].lstrip()
    return rest or prose


def beats_block(all_beats: list[dict], beat_index: int) -> str:
    """The scene's whole beat plan, numbered from 1, with the current beat
    marked — so the model knows where its beat ends (SPEC-18, B5)."""
    lines = []
    for i, b in enumerate(all_beats):
        mark = f"{CURRENT_BEAT_MARK} " if i == beat_index else ""
        description = clean_for_llm(b.get("description") if isinstance(b, dict) else b)
        lines.append(f"{mark}{i + 1}. {description}")
    return as_quoted_block("\n".join(lines), BEATS_FENCE)


def _style_section(style) -> str:
    """Style guidance, the author's first (SPEC-18, B5).

    Sample prose is the primary reference whenever it exists; a style-guide
    tone alone replaces the house style; the generic craft text appears only
    when the book gives neither. The sample and the tone themselves are in the
    canon block's style guide (`render_style`), so they are referred to here,
    never repeated (W1-S3)."""
    if style is not None and clean_for_llm(getattr(style, "sample_prose", None)):
        return (
            "STYLE — the author's sample prose (the canon's style guide, above) "
            "is your primary style reference. Match its voice and rhythm: its "
            "sentence length, its balance of dialogue and narration, its diction."
        )
    if style is not None and clean_for_llm(getattr(style, "tone", None)):
        return (
            "STYLE — follow the tone in the canon's style guide, above. Let it "
            "decide how much the scene leans on dialogue or on description."
        )
    return GENERIC_CRAFT_GUIDANCE


async def write_beat_prose(
    beat: dict,
    scene_request: dict,
    cast_context: str,
    prior_prose_tail: str,
    *,
    pov: Pov,
    story_position: Optional[StoryPosition],
    secondary_block: str,
    chapter_summary: str = "",
    canon_block: str = "",
    user_id: Optional[UUID] = None,
    target_words: int = 300,
    all_beats: Optional[list[dict]] = None,
    beat_index: Optional[int] = None,
    style=None,
) -> str:
    """Write one beat's full prose (narration + in-voice dialogue) in one call.

    Prompt layout: a stable prefix (notice, whole canon, chapter) that is the
    same for every beat of the scene, then the per-beat suffix (scene brief,
    the scene's beat plan, the beat's cast, the prose so far, the beat itself,
    then style). `all_beats`/`beat_index` give the model the whole plan with
    this beat marked, so it stops where the next begins; `style` is the book's
    style guide, whose sample prose leads the style guidance (SPEC-18).
    `pov` is `resolve_pov`'s; the prompt states its mode and character and
    keeps the narration in that character's head (SPEC-24). The story position
    follows the chapter (SPEC-30), the secondaries the cast (SPEC-29).
    """
    style_notes = scene_request.get("style_notes") or ""
    prior_block = (
        "The prose so far (continue seamlessly from it; do not restate "
        "anything in <prior>, begin with what happens next):\n"
        + as_quoted_block(prior_prose_tail, PRIOR_FENCE)
        if prior_prose_tail
        else "This beat opens the scene."
    )
    plan = ""
    stop_line = ""
    if all_beats and beat_index is not None and 0 <= beat_index < len(all_beats):
        n, total = beat_index + 1, len(all_beats)
        plan = (
            f"The scene's beats, in order (→ marks beat {n}, the one you are "
            f"writing):\n{beats_block(all_beats, beat_index)}\n\n"
        )
        if n < total:
            stop_line = (
                f"\nWrite ONLY beat {n}. {STOP_AT_NEXT_BEAT.format(next=n + 1)}; do "
                "not anticipate it."
            )
        else:
            stop_line = f"\nWrite ONLY beat {n}, the last beat: {LAST_BEAT_ENDING}."
    secondaries = f"\n\n{secondary_block}" if secondary_block else ""
    overrides = (
        "\nAuthor's style notes for THIS scene (they override the style guide):\n"
        + as_quoted_block(style_notes, "style_notes")
        if style_notes
        else ""
    )

    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_block}

{chapter_context(chapter_summary, story_position)}

You are a skilled novelist writing one beat of a scene in THIS book. Stay
faithful to the canon above: its people, places, terms, and its style guide
(point of view, tense, tone) unless the author's notes below override it.

{scene_block(scene_request)}
{pov_instruction(pov)}
{NAMING_RULE}{overrides}

{plan}{CHARACTERS_SECTION} — write each one's dialogue in THEIR OWN voice,
matching their voice samples:

{cast_context}{secondaries}

{prior_block}

{BEAT_TO_WRITE_LABEL}: {clean_for_llm(beat["description"])}{stop_line}

{_style_section(style)}

Write approximately {target_words} words of polished NARRATIVE PROSE. When a
character does speak, give each a DISTINCT voice — diction, cadence, and syntax
differing enough that a reader could tell who is speaking without the tags. Do
NOT use headings, beat labels, character-name prefixes (e.g. "Milo:"), or stage
directions — just the prose. Do NOT repeat the prose so far.

Prose:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=max(600, int(target_words * 2.2)),
        user_id=user_id,
        purpose="beat_prose",
    )
    return result.text


async def run_prose_scene_workflow(
    scene_id: UUID,
    scene_request: dict,
    user_id: UUID,
    *,
    chapter_summary: str,
    story_position: Optional[StoryPosition],
    prior_scene_tail: str = "",
    book_id: Optional[UUID],
    checkpoint: Optional[dict] = None,
    save_checkpoint=None,
    on_restart=None,
) -> dict:
    """Prose-mode workflow for a Scene row the API layer already created.

    `checkpoint`/`save_checkpoint` make a resumed job continue from its last
    completed model call instead of re-spending every beat (SPEC-08);
    `on_restart(note)` records why a checkpoint was discarded.
    """
    started = datetime.now(timezone.utc)
    checkpoint = dict(checkpoint or {})
    try:
        if book_id is None:
            raise RuntimeError("scene generation needs its book (book is the root)")
        async with get_async_session() as session:
            setup = await scene_setup(session, book_id, scene_request, scene_id)
        canon, cast, scope, pov, secondary_block, *_ = setup
        canon_block = canon.full_block()
        request = {**scene_request, "characters": [c.name for c in cast]}
        canon_hash = _canon_hash(
            setup, chapter_summary, story_position, prior_scene_tail
        )

        if checkpoint.get("canon_hash") not in (None, canon_hash):
            # The canon changed while the job was paused: stages generated
            # against two canons would be worse than paying twice.
            logger.info(
                "Canon changed during pause; restarting scene from scratch",
                extra_fields={"event": "scene_checkpoint_discarded"},
            )
            checkpoint = {}
            if on_restart is not None:
                await on_restart(CANON_CHANGED_NOTE)

        beats = checkpoint.get("beats")
        if beats is None:
            beats = await plan_scene_beats(
                request,
                user_id,
                canon_block=canon_block,
                chapter_summary=chapter_summary,
                pov=pov,
                story_position=story_position,
                secondary_block=secondary_block,
                plan=setup.plan,
                earlier=setup.earlier,
            )
            checkpoint = {
                "canon_hash": canon_hash,
                "beats": beats,
                "prose": [],
                "progress": {"stage": "beats", "done": 0, "total": len(beats)},
            }
            await _save(save_checkpoint, checkpoint)
        scope = scope._replace(plot=scope.plot + tuple(b["description"] for b in beats))

        target_total = int(scene_request.get("target_word_count") or 500)
        per_beat_words = max(120, target_total // max(1, len(beats)))
        by_name = {c.name: c for c in cast}

        prose_parts: list[str] = list(checkpoint.get("prose") or [])
        grounding: dict = dict(checkpoint.get("grounding") or {})
        prior_tail = prior_scene_tail
        if prose_parts:
            prior_tail = prose_tail("\n\n".join(prose_parts))

        for idx in range(len(prose_parts), len(beats)):
            beat = beats[idx]
            beat_cast = [by_name[n] for n in beat["characters"] if n in by_name]
            beat_grounding: list = []
            cast_context = await build_cast_context(
                beat_cast, beat["description"], scope=scope, grounding=beat_grounding
            )
            prose = await write_beat_prose(
                beat,
                request,
                cast_context,
                prior_tail,
                chapter_summary=chapter_summary,
                user_id=user_id,
                target_words=per_beat_words,
                canon_block=canon_block,
                pov=pov,
                story_position=story_position,
                secondary_block=secondary_block,
                all_beats=beats,
                beat_index=idx,
                style=getattr(canon, "style", None),
            )
            prose_parts.append(trim_seam_repeat(prose, prior_tail))
            grounding[str(idx)] = beat_grounding
            prior_tail = prose_tail("\n\n".join(prose_parts))
            checkpoint.update(
                prose=prose_parts,
                grounding=grounding,
                beat_index=idx,
                progress={
                    "stage": "beats",
                    "done": len(prose_parts),
                    "total": len(beats),
                },
            )
            await _save(save_checkpoint, checkpoint)

        scene_text = "\n\n".join(prose_parts)
        word_count = len(scene_text.split())
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        # A safety block / truncation can make every beat return "" — don't record
        # blank output as a successful, editable draft.
        if word_count < 10:
            raise RuntimeError(
                f"scene generation produced no usable prose ({word_count} words)"
            )

        async with get_async_session() as session:
            scene = await session.get(Scene, scene_id)
            if scene is None:
                raise RuntimeError(f"Scene {scene_id} disappeared mid-workflow")
            scene.generated_content = scene_text
            scene.content = scene_text  # initial editable draft = the generation
            scene.word_count = word_count
            scene.status = "completed"
            scene.generation_time_ms = elapsed_ms
            scene.evaluation_scores = {
                **(scene.evaluation_scores or {}),
                "mode": "prose",
                "grounding": grounding,
                "name_warnings": scene_name_warnings(
                    scene_text, canon, scene_request, prior_scene_tail
                ),
            }
            for beat_idx, beat in enumerate(beats):
                session.add(
                    SceneBeat(
                        scene_id=scene.id,
                        beat_number=beat_idx,
                        description=beat["description"],
                        content=prose_parts[beat_idx],
                    )
                )

        log_business_event(
            logger,
            "prose_scene_completed",
            scene_id=str(scene_id),
            beats_count=len(beats),
            word_count=word_count,
            llm_calls=1 + len(beats),
        )
        return {
            "scene_id": str(scene_id),
            "status": "completed",
            "beats_count": len(beats),
        }

    except QuotaExhaustedError:
        # Not a failure — the quota is temporarily gone. Let the error propagate
        # so the worker PAUSES the job and resumes it (from its checkpoint) when
        # quota returns (docs/BRD.md R7.2).
        raise
    except Exception as e:
        from app.jobs.cancel import JobCancelled

        if isinstance(e, JobCancelled):
            raise
        log_error(
            logger,
            e,
            context={"scene_id": str(scene_id), "event": "prose_scene_failed"},
        )
        try:
            async with get_async_session() as session:
                scene = await session.get(Scene, scene_id)
                if scene is not None:
                    scene.status = "failed"
        except Exception:
            pass
        return {"scene_id": str(scene_id), "status": "failed", "error": str(e)}


def _canon_hash(setup: SceneSetup, summary: str, position, prior_tail="") -> str:
    """Fingerprint of everything a checkpointed beat was written against: the
    canon and secondary block, the chapter and its story position, and the
    prose of the scene before this one (SPEC-29/30). Editing any of them while
    the job is paused restarts it rather than continuing from beats that no
    longer follow. The book's plan and the chapter's earlier beats the planner
    sees (SPEC-43) count too, when there are any, so an unplanned scene keeps
    its old fingerprint."""
    import hashlib
    import json

    # Hash the tail's last PREV_TAIL_WORDS words, whitespace-normalised: that
    # is exactly what the pre-SPEC-18 flattened tail was, and `prose_tail`
    # always ends with those same words, so a checkpoint saved before the
    # paragraph-preserving tail still matches (no needless restart).
    tail = " ".join((prior_tail or "").split()[-PREV_TAIL_WORDS:])
    canon = setup.canon.full_block() + setup.secondary_block
    parts = (canon, (summary or "") + story_position_line(position), tail)
    if setup.plan or setup.earlier:
        parts += (json.dumps([setup.plan, setup.earlier], sort_keys=True),)
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


async def _save(save_checkpoint, data: dict) -> None:
    if save_checkpoint is not None:
        await save_checkpoint(data)
