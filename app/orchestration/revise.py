"""AI revision of a scene span (SPEC-19, backlog B3).

The author selects a span of the draft (or a beat, or the whole scene) and asks
for a revision ("tighten this", "make Zara colder"). One model call rewrites
ONLY that span: it reads the whole canon, the chapter, the scene's cast with
their voice samples, and the rest of the draft as read-only `<prior>` /
`<after>` context, so the new span still fits its surroundings.

The result is spliced server-side — `before + revised + after` — so the text
outside the span is byte-identical by construction, whatever the model returns.
It is stored as a proposal (`SceneRevision(source="ai_revise")`); the draft is
not touched until the author accepts it (app/api/revise.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Sequence
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.core.llm_text import STORY_MATERIAL_NOTICE, as_quoted_block, clean_for_llm
from app.core.orm_models import Scene, SceneBeat
from app.core.prompt_markers import (
    CURRENT_BEAT_MARK,
    PRIOR_FENCE,
    SCENE_CHARACTERS_SECTION,
    TIGHTEN_CLAUSE,
)
from app.llm.client import get_llm_client
from app.orchestration.pov import NAMING_RULE, Pov, pov_instruction
from app.orchestration.prose import beats_block
from app.orchestration.workflow import StoryPosition, chapter_context
from app.planning.fidelity import canon_known_text, unknown_proper_nouns

# The stub LLM (evals/stub_llm.py) recognises the revise family by this line.
OPENING = "Revise the TARGET passage of a scene in THIS book, as the author asks."
PURPOSE = "scene_revise"
# The job that runs a revision (app/jobs/revise.py), named here so the API
# enqueues it without importing the job module (API -> orchestration <- job).
JOB_KIND = "revise_scene"


class SpanError(ValueError):
    """The requested span cannot be resolved against the current draft. The
    message is shown to the author; `status` is the HTTP status to answer."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def text_hash(text: str) -> str:
    """Fingerprint of a draft: a proposal is valid only against the draft it
    was written from."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class CharRange(BaseModel):
    """Character offsets into the draft as GET /proposals returns it (`draft`),
    end exclusive. Python string indices, i.e. Unicode code points — not
    UTF-16 code units, which is what a JS string index counts."""

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("range.end must be greater than range.start")
        return self


class SpanSpec(BaseModel):
    """What to revise: a character range, a beat (its index), or neither for
    the whole scene. The revise request and the estimate request both embed
    it, so the rule is stated once."""

    range: Optional[CharRange] = None
    beat_index: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _one_span(self):
        if self.range is not None and self.beat_index is not None:
            raise ValueError("send range OR beat_index (or neither, for the scene)")
        return self


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def split(self, draft: str) -> tuple[str, str, str]:
        return draft[: self.start], draft[self.start : self.end], draft[self.end :]


def resolve_span(
    draft: str,
    *,
    range_: Optional[tuple[int, int]] = None,
    beat_text: Optional[str] = None,
) -> Span:
    """The span to revise: an explicit character range, a beat's prose located
    verbatim in the draft, or (neither) the whole scene.

    Offsets are Python string indices (Unicode code points) into the draft as
    the API returns it.
    """
    if not draft:
        raise SpanError("The scene has no text to revise yet.")
    if range_ is not None:
        start, end = range_
        if not (0 <= start < end <= len(draft)):
            raise SpanError(
                f"The range {start}..{end} is outside the draft "
                f"(0..{len(draft)}); reload the scene and select again.",
                status=422,
            )
        return Span(start, end)
    if beat_text is not None:
        needle = beat_text.strip()
        at = draft.find(needle) if needle else -1
        if at < 0:
            raise SpanError("The beat was edited; select the text instead.")
        if draft.find(needle, at + 1) >= 0:
            # Two beats wrote the same prose: which one is meant is ambiguous.
            raise SpanError(
                "That beat's text appears more than once; select the text instead."
            )
        return Span(at, at + len(needle))
    return Span(0, len(draft))


async def resolve_scene_span(db, scene: Scene, spec: SpanSpec) -> Span:
    """`spec` resolved against the scene's current draft (`Scene.draft`). A
    beat is located by its stored prose; SpanError (404) when the scene has no
    such beat, or the beat has no prose."""
    if spec.beat_index is None:
        rng = (spec.range.start, spec.range.end) if spec.range else None
        return resolve_span(scene.draft, range_=rng)
    beat = (
        await db.execute(
            select(SceneBeat).where(
                SceneBeat.scene_id == scene.id,
                SceneBeat.beat_number == spec.beat_index,
            )
        )
    ).scalar_one_or_none()
    if beat is None or not (beat.content or "").strip():
        raise SpanError("Beat not found", status=404)
    return resolve_span(scene.draft, beat_text=beat.content)


def cast_request(scene: Scene) -> dict:
    """The scene's cast as a request dict `scene_cast` resolves against the
    canon. A manual scene has no generation request; its own cast fields do."""
    if scene.scene_request:
        return scene.scene_request
    return {
        "character_ids": scene.character_ids or [],
        "characters": scene.characters or [],
    }


def splice(draft: str, span: Span, revised: str) -> str:
    """The proposal: everything outside the span exactly as it was."""
    before, target, after = span.split(draft)
    return before + _keep_edges(target, revised) + after


def _keep_edges(target: str, revised: str) -> str:
    """Re-attach the span's own leading/trailing whitespace (a selection that
    ends on a paragraph break keeps it), so the seam with the untouched text
    is the author's, not the model's."""
    lead = target[: len(target) - len(target.lstrip())]
    trail = target[len(target.rstrip()) :]
    return lead + revised.strip() + trail


_FENCE_LINE = re.compile(r"^```[\w-]*\s*$")
_TAG = re.compile(r"^\s*</?target>\s*$")
_LABEL = re.compile(r"^\s*(?:revised (?:target )?passage|revision)\s*:\s*", re.I)


def clean_revision(text: str) -> str:
    """The model's answer as plain prose: no code fences, no echoed <target>
    tags, no leading "Revised passage:" label."""
    lines = [
        ln
        for ln in (text or "").replace("\r\n", "\n").split("\n")
        if not _FENCE_LINE.match(ln) and not _TAG.match(ln)
    ]
    out = "\n".join(lines).strip()
    return _LABEL.sub("", out, count=1).strip()


# A redo (SPEC-41, SPEC-47).
KEEP_LINE = "Keep these (the rest of this chapter refers to them): {terms}"
REDO_DIRECTION = (
    "If the instruction does not say what to change, keep the beat's events and "
    "facts and change only the telling."
)
# An instruction is a direction when more than its "Redo beat N." remains.
REDO_CHANGE = (
    "Change what the instruction asks for. Keep every other sentence of the beat "
    "as written, unless it must change to fit the change."
)
_REDO_LEAD = re.compile(r"^\s*redo beat \d+\s*[.:]?", re.I)
# A cut request (SPEC-59): what is asked, its "Redo beat N" lead removed, names
# one of these words. The product's rule alone; no grader reads it.
_CUT_WORDS = re.compile(r"\b(?:cut|tighten|trim|shorten|condense|pare)\b", re.I)
DROPPED_CALLBACK = (
    "This version drops {terms}, which the rest of this chapter refers to."
)
# The callback classes, each pinned by an archived example (SPEC-41): hyphenated
# compounds, numbers, a number word with the next word of its sentence (SPEC-47:
# "nine days", never a bare "two"), dash-led fragments, capitals.
_NUMBER_WORDS = (
    "two three four five six seven eight nine ten eleven twelve twenty thirty "
    "forty fifty sixty seventy eighty ninety hundred thousand million"
).split()
_CALLBACK = re.compile(
    r"\b\w+(?:-\w+)+\b|\b\d[\d,.]*\b|\b(?i:" + "|".join(_NUMBER_WORDS) + r")[ \t]+\w+"
    r"|(?<![\w—-])—[a-z][\w'’-]*"
    r"|(?P<cap>(?<![\w'’-])[A-Z][a-z][\w'’-]*(?: [A-Z][\w'’-]*)*)"
)


def _opens_sentence(text: str, at: int) -> bool:
    before = text[:at].rstrip(" \t\n\"“”'‘’(—–-")
    return not before or before[-1] in ".!?…:"


def _use(text: str, term: str):
    """The first use of `term` as a whole word or phrase of `text`, ignoring
    case; a dash-led fragment by its words."""
    bare = re.escape(term.lstrip("—"))
    return re.search(rf"(?<![\w-]){bare}(?![\w-])", text, re.I)


_SENTENCE_END = re.compile(r"[.!?…][\"”’')\]]*\s|\n")


def callback_context(term: str, rest: str) -> str:
    """Where `rest` first uses `term` (SPEC-47): its sentence's words from up
    to three before it to up to three after, at most 8, outer punctuation
    trimmed."""
    m = _use(rest, term)
    ends = _SENTENCE_END.finditer(rest, 0, m.start())
    start = max((e.end() for e in ends), default=0)
    stop = _SENTENCE_END.search(rest, m.end())
    words = re.finditer(r"\S+", rest[: stop.start() + 1 if stop else None])
    words = [w for w in words if w.end() > start]
    at = [i for i, w in enumerate(words) if w.end() > m.start() and w.start() < m.end()]
    span = words[max(0, at[0] - 3) : at[-1] + 4][:8]
    return " ".join(w.group(0) for w in span).strip(" \"“”'‘’()[]—–-,;:.!?…")


def callbacks(target: str, rest: str, canon) -> list[str]:
    """The ONE callback rule (SPEC-41): the target's distinctive terms that
    `rest` (the scene around it, the chapter's later scenes) also uses. Never a
    canon term, nor a capitalised word that opens a sentence."""
    known = {t.lower() for t in canon.canon_terms()}
    canon_text = canon_known_text(canon)
    found: dict[str, str] = {}
    for m in _CALLBACK.finditer(target):
        term = m.group(0)
        if m.lastgroup == "cap":  # "Appendix F's" is the callback "Appendix F"
            term = re.sub(r"['’]s$", "", term)
        key = term.lower()
        if key in found or key in known or known & set(key.split()):
            continue
        if m.lastgroup == "cap" and (
            _opens_sentence(target, m.start())
            or re.search(rf"\b{re.escape(term)}\b", canon_text)
        ):
            continue
        if _use(rest, term):
            found[key] = term
    return list(found.values())


def dropped_callbacks(revised: str, keep: list[str]) -> list[str]:
    """The `keep` terms the revised span no longer uses (warn-only)."""
    return [t for t in keep if not _use(revised, t)]


def build_revise_prompt(
    *,
    canon_block: str,
    chapter_summary: str,
    cast_context: str,
    prior: str,
    target: str,
    after: str,
    instruction: str,
    pov: Pov,
    story_position: Optional[StoryPosition],
    secondary_block: str,
    beat: Optional[tuple[int, list[str]]] = None,
    keep: Sequence[tuple[str, str]] = (),
) -> str:
    """The revise prompt. Everything the author wrote reaches the model whole:
    the canon, the chapter and its place in the book, the cast's and the
    secondaries' voice context and the entire draft (prior + target + after) —
    nothing is cut to fit (canon invariant 1).

    `beat` is (beat_index, the scene's beat lines) when the author asked to
    redo a beat: the prompt names the beat and shows the scene's whole plan
    with it marked, then `keep` (what the rest of the chapter refers to, as
    (term, context)) and how much to change (SPEC-41, SPEC-47). A cut request
    also gets `TIGHTEN_CLAUSE`: a direction, never a quota (SPEC-59)."""
    asked = _REDO_LEAD.sub("", instruction).strip()
    tighten = f"\n{TIGHTEN_CLAUSE}" if _CUT_WORDS.search(asked) else ""
    plan_line = ""
    if beat:
        index, lines = beat
        description = " ".join(clean_for_llm(lines[index]).split())
        terms = ", ".join(f'{t} ("{c}")' for t, c in keep)
        plan_line = "\n".join(
            [
                f"\nBeat {index + 1} of this scene's plan: {description}",
                f"The scene's whole plan ({CURRENT_BEAT_MARK} marks beat {index + 1}):",
                beats_block(lines, index),
                *([KEEP_LINE.format(terms=terms)] if keep else []),
                REDO_CHANGE if asked else REDO_DIRECTION,
            ]
        )
    prior_block = (
        "The scene BEFORE the target (read-only: do not rewrite, repeat or "
        "summarise it):\n" + as_quoted_block(prior, PRIOR_FENCE)
        if prior.strip()
        else "The target opens the scene."
    )
    after_block = (
        "The scene AFTER the target (read-only: your revision must lead into it "
        "seamlessly; do not rewrite or repeat it):\n" + as_quoted_block(after, "after")
        if after.strip()
        else "The target ends the scene."
    )
    voices = "\n\n".join(p for p in (cast_context, secondary_block) if p)
    cast_block = (
        f"{SCENE_CHARACTERS_SECTION} — keep each one's dialogue in THEIR OWN voice, "
        "matching their voice samples:\n\n" + voices
        if voices
        else ""
    )
    return f"""{OPENING}

{STORY_MATERIAL_NOTICE}
The one exception is the <instruction> block: it is the author's own request
to you about the TARGET passage. Follow it for the TARGET only.

{canon_block}

{chapter_context(chapter_summary, story_position)}

{cast_block}

{pov_instruction(pov)}
{NAMING_RULE}{plan_line}

{prior_block}

TARGET passage to revise:
{as_quoted_block(target, "target")}

{after_block}

The author's instruction:
{as_quoted_block(instruction, "instruction")}

Rewrite ONLY the TARGET passage so that it follows the instruction. Stay
faithful to the canon above: its people, places, terms and its style guide
(point of view, tense, tone). Keep the events, and the facts, people and
objects the surrounding text depends on. Do not add headings, labels,
quotation of the instruction, or commentary — return the revised passage
itself, and nothing else.{tighten}

Revised passage:"""


async def generate_revision(
    prompt: str, *, target_chars: int, user_id: Optional[UUID]
) -> str:
    """One model call. QuotaExhaustedError propagates so the worker pauses the
    job (R7.2)."""
    from app.core.estimates import revise_max_tokens

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=revise_max_tokens(target_chars),
        user_id=user_id,
        purpose=PURPOSE,
    )
    revised = clean_revision(result.text)
    if not revised:
        raise RuntimeError("the revision came back empty")
    return revised


def new_names(revised: str, draft: str, canon) -> list[str]:
    """Proper nouns in the revised span that are neither canon terms nor named
    in the canon or the draft: a possible invented character. Warn-only (canon
    invariant 4), using the outline audit's check and its known text plus the
    draft, so a name the synopsis uses is known ("God help him", M5)."""
    return unknown_proper_nouns(
        revised,
        canon.canon_terms(),
        canon_text=canon_known_text(canon) + "\n" + draft,
    )
