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
from typing import Optional
from uuid import UUID

from app.characters.context import build_cast_context
from app.core.database import get_async_session
from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Scene, SceneBeat
from app.llm.client import get_llm_client
from app.llm.errors import QuotaExhaustedError

from .workflow import plan_scene_beats, scene_block

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


def resolve_pov(scene_request: dict, canon) -> str:
    """The request's POV wins; else the book's style guide; else a default."""
    if scene_request.get("pov_character"):
        return clean_for_llm(scene_request["pov_character"])
    style = getattr(canon, "style", None)
    if style is not None and getattr(style, "pov", None):
        return f"as the book's style guide specifies ({clean_for_llm(style.pov)})"
    return "third person limited"


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


def _beats_block(all_beats: list[dict], beat_index: int) -> str:
    """The scene's whole beat plan, numbered from 1, with the current beat
    marked — so the model knows where its beat ends (SPEC-18, B5)."""
    lines = []
    for i, b in enumerate(all_beats):
        mark = "→ " if i == beat_index else ""
        description = clean_for_llm(b.get("description") if isinstance(b, dict) else b)
        lines.append(f"{mark}{i + 1}. {description}")
    return as_quoted_block("\n".join(lines), "beats")


def _style_section(style) -> str:
    """Style guidance, the author's first (SPEC-18, B5).

    Sample prose is the primary reference whenever it exists; a style-guide
    tone alone replaces the house style; the generic craft text appears only
    when the book gives neither."""
    sample = clean_for_llm(getattr(style, "sample_prose", None)) if style else ""
    tone = clean_for_llm(getattr(style, "tone", None)) if style else ""
    if sample:
        out = (
            "STYLE — the author's sample prose is your primary style reference. "
            "Match this sample's voice and rhythm: its sentence length, its "
            "balance of dialogue and narration, its diction.\n"
            + as_quoted_block(sample, "sample_prose")
        )
        if tone:
            out += f"\nTone: {tone}"
        return out
    if tone:
        return (
            f"STYLE — follow the book's style guide. Tone: {tone}. Let it decide "
            "how much the scene leans on dialogue or on description."
        )
    return GENERIC_CRAFT_GUIDANCE


async def write_beat_prose(
    beat: dict,
    scene_request: dict,
    cast_context: str,
    prior_prose_tail: str,
    chapter_summary: str = "",
    user_id: Optional[UUID] = None,
    target_words: int = 300,
    *,
    canon_block: str = "",
    pov: str = "third person limited",
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
    """
    style_notes = scene_request.get("style_notes") or ""
    prior_block = (
        "The prose so far (continue seamlessly from it; do not restate "
        "anything in <prior>, begin with what happens next):\n"
        + as_quoted_block(prior_prose_tail, "prior")
        if prior_prose_tail
        else "This beat opens the scene."
    )
    plan = ""
    position = ""
    if all_beats and beat_index is not None and 0 <= beat_index < len(all_beats):
        n, total = beat_index + 1, len(all_beats)
        plan = (
            f"The scene's beats, in order (→ marks beat {n}, the one you are "
            f"writing):\n{_beats_block(all_beats, beat_index)}\n\n"
        )
        if n < total:
            position = (
                f"\nWrite ONLY beat {n}. Stop where beat {n + 1} begins; do not "
                "anticipate it."
            )
        else:
            position = f"\nWrite ONLY beat {n}, the last beat: end the scene here."
    chapter = as_quoted_block(chapter_summary, "chapter")
    overrides = (
        "\nAuthor's style notes for THIS scene (they override the style guide):\n"
        + as_quoted_block(style_notes, "style_notes")
        if style_notes
        else ""
    )

    prompt = f"""{STORY_MATERIAL_NOTICE}

{canon_block}

{chapter}

You are a skilled novelist writing one beat of a scene in THIS book. Stay
faithful to the canon above: its people, places, terms, and its style guide
(point of view, tense, tone) unless the author's notes below override it.

{scene_block(scene_request)}
Point of view: {pov}{overrides}

{plan}CHARACTERS IN THIS BEAT — write each one's dialogue in THEIR OWN voice,
matching their voice samples:

{cast_context}

{prior_block}

BEAT TO WRITE: {clean_for_llm(beat["description"])}{position}

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
    chapter_summary: str = "",
    prior_scene_tail: str = "",
    book_id: Optional[UUID] = None,
    *,
    checkpoint: Optional[dict] = None,
    save_checkpoint=None,
    on_restart=None,
) -> dict:
    """Prose-mode workflow for a Scene row the API layer already created.

    `checkpoint`/`save_checkpoint` make a resumed job continue from its last
    completed model call instead of re-spending every beat (SPEC-08);
    `on_restart(note)` records why a checkpoint was discarded.
    """
    from app.planning.canon import build_canon

    started = datetime.now(timezone.utc)
    checkpoint = dict(checkpoint or {})
    try:
        if book_id is None:
            raise RuntimeError("scene generation needs its book (book is the root)")
        async with get_async_session() as session:
            canon = await build_canon(session, book_id)
        canon_block = canon.full_block()
        cast = scene_cast(canon, scene_request)
        names = [c.name for c in cast]
        request = {**scene_request, "characters": names}
        pov = resolve_pov(scene_request, canon)
        canon_hash = _canon_hash(canon_block, chapter_summary, prior_scene_tail)

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
            )
            checkpoint = {
                "canon_hash": canon_hash,
                "beats": beats,
                "prose": [],
                "progress": {"stage": "beats", "done": 0, "total": len(beats)},
            }
            await _save(save_checkpoint, checkpoint)

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
                beat_cast, beat["description"], grounding=beat_grounding
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


def _canon_hash(
    canon_block: str, chapter_summary: str, prior_scene_tail: str = ""
) -> str:
    """Fingerprint of everything a checkpointed beat was written against: the
    canon, the chapter, and the prose of the scene before this one. Editing any
    of them while the job is paused restarts it rather than continuing from
    beats that no longer follow."""
    import hashlib

    # Hash the tail's last PREV_TAIL_WORDS words, whitespace-normalised: that
    # is exactly what the pre-SPEC-18 flattened tail was, and `prose_tail`
    # always ends with those same words, so a checkpoint saved before the
    # paragraph-preserving tail still matches (no needless restart).
    tail = " ".join((prior_scene_tail or "").split()[-PREV_TAIL_WORDS:])
    parts = (canon_block, chapter_summary or "", tail)
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


async def _save(save_checkpoint, data: dict) -> None:
    if save_checkpoint is not None:
        await save_checkpoint(data)
